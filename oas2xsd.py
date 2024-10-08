#!/usr/bin/env python

import sys
import yaml
import xml.etree.ElementTree as ET
import argparse
import os

class OpenAPIToXSDConverter:
    def __init__(self, openapi_spec):
        self.openapi_spec = openapi_spec
        self.global_types = {}  # Store globally defined types
        self.type_names = set()  # Keep track of type names to avoid duplicates
        self.anonymous_type_count = 0  # Counter for anonymous types

    def yaml_type_to_xsd_type(self, yaml_type):
        """Map OpenAPI YAML scalar types to XSD types."""
        mapping = {
            "string": "xs:string",
            "integer": "xs:int",
            "boolean": "xs:boolean",
            "number": "xs:decimal"
        }
        return mapping.get(yaml_type, "xs:string")

    def create_enum_restriction(self, base_type, enum_values):
        """Create an XSD simpleType with a restriction for enum values."""
        restriction = ET.Element('xs:restriction', base=base_type)
        for value in enum_values:
            ET.SubElement(restriction, 'xs:enumeration', value=value)
        simple_type = ET.Element('xs:simpleType')
        simple_type.append(restriction)
        return simple_type

    def create_xsd_element(self, name, element_type=None, required=False, is_array=False,
                           complex_type=None, enum_values=None, ref=None):
        """Create an XML Schema element."""
        element = ET.Element('xs:element', name=name)
        element.set("minOccurs", "1" if required else "0")
        if is_array:
            element.set("maxOccurs", "unbounded")

        if ref:
            element.set('ref', ref)
        elif enum_values:
            simple_type = self.create_enum_restriction(element_type, enum_values)
            element.append(simple_type)
        elif complex_type is not None:
            element.append(complex_type)
        elif element_type:
            element.set('type', element_type)
        else:
            element.set('type', 'xs:string')

        return element

    def resolve_ref(self, ref_path):
        """Resolve a $ref path to its corresponding schema definition in the OpenAPI spec."""
        ref_parts = ref_path.strip('#/').split('/')
        schema = self.openapi_spec
        for part in ref_parts:
            schema = schema.get(part, {})
        return schema

    def find_request_body_only_types(self):
        """Find types used exclusively in request bodies."""
        request_body_types = set()
        schemas = set(self.openapi_spec.get('components', {}).get('schemas', {}).keys())
        paths = self.openapi_spec.get('paths', {})

        # Collect types used in request bodies
        for path_item in paths.values():
            for operation in path_item.values():
                request_body = operation.get('requestBody', {})
                content = request_body.get('content', {})
                for media_type in content.values():
                    schema = media_type.get('schema', {})
                    ref = schema.get('$ref')
                    if ref:
                        schema_name = ref.split('/')[-1]
                        if schema_name in schemas:
                            request_body_types.add(schema_name)

        # Collect types used elsewhere (responses, parameters)
        used_types = set()
        for path_item in paths.values():
            for operation in path_item.values():
                # Responses
                responses = operation.get('responses', {})
                for response in responses.values():
                    content = response.get('content', {})
                    for media_type in content.values():
                        schema = media_type.get('schema', {})
                        ref = schema.get('$ref')
                        if ref:
                            schema_name = ref.split('/')[-1]
                            used_types.add(schema_name)
                # Parameters
                parameters = operation.get('parameters', [])
                for parameter in parameters:
                    schema = parameter.get('schema', {})
                    ref = schema.get('$ref')
                    if ref:
                        schema_name = ref.split('/')[-1]
                        used_types.add(schema_name)

        # Types used exclusively in request bodies
        exclusive_request_body_types = request_body_types - used_types
        return exclusive_request_body_types

    def collect_all_types(self):
        """Collect all types from the OpenAPI spec and assign unique names."""
        self.global_types = {}
        self.type_names = set()
        schemas = self.openapi_spec.get('components', {}).get('schemas', {})
        # Collect top-level schemas
        for schema_name, schema in schemas.items():
            self.global_types[schema_name] = schema
            self.type_names.add(schema_name)
        # Collect nested schemas
        for schema in schemas.values():
            self.collect_nested_types(schema)

    def collect_nested_types(self, schema):
        """Recursively collect nested types."""
        if schema.get('type') == 'object':
            properties = schema.get('properties', {})
            for prop_name, prop in properties.items():
                if '$ref' in prop:
                    # Already handled via $ref
                    continue
                elif prop.get('type') == 'object':
                    # Assign a unique name for the nested object
                    type_name = self.get_unique_type_name(prop_name)
                    self.global_types[type_name] = prop
                    self.type_names.add(type_name)
                    # Replace the property definition with a $ref
                    prop.clear()
                    prop['$ref'] = f"#/components/schemas/{type_name}"
                    # Recursively collect nested types
                    self.collect_nested_types(self.global_types[type_name])
                elif prop.get('type') == 'array':
                    items = prop.get('items', {})
                    if '$ref' in items:
                        continue
                    elif items.get('type') == 'object':
                        # Assign a unique name for the array items type
                        type_name = self.get_unique_type_name(prop_name + 'Item')
                        self.global_types[type_name] = items
                        self.type_names.add(type_name)
                        # Replace the items definition with a $ref
                        items.clear()
                        items['$ref'] = f"#/components/schemas/{type_name}"
                        # Recursively collect nested types
                        self.collect_nested_types(self.global_types[type_name])

    def get_unique_type_name(self, base_name):
        """Generate a unique type name based on base_name."""
        type_name = base_name
        index = 1
        while type_name in self.type_names:
            type_name = f"{base_name}_{index}"
            index += 1
        return type_name

    def merge_all_of_schemas(self, all_of_list):
        """Merge multiple schemas defined in an allOf list into a single schema."""
        merged_properties = {}
        required_fields = set()
        references = []

        for schema in all_of_list:
            if '$ref' in schema:
                ref_schema = self.resolve_ref(schema['$ref'])
                props, reqs, refs = self.process_ref_or_schema(ref_schema)
                merged_properties.update(props)
                required_fields.update(reqs)
                references.extend(refs)
            else:
                props, reqs, refs = self.process_ref_or_schema(schema)
                merged_properties.update(props)
                required_fields.update(reqs)
                references.extend(refs)

        return merged_properties, list(required_fields), references

    def process_ref_or_schema(self, schema):
        """Process a schema or $ref, returning its properties, required fields, and references."""
        properties = {}
        required_fields = []
        references = []

        if '$ref' in schema:
            ref_schema = self.resolve_ref(schema['$ref'])
            return self.process_ref_or_schema(ref_schema)
        elif 'allOf' in schema:
            properties, required_fields, references = self.merge_all_of_schemas(schema['allOf'])
        else:
            properties = schema.get('properties', {})
            required_fields = schema.get('required', [])
            # Collect references from properties
            for prop in properties.values():
                if '$ref' in prop:
                    ref_name = prop['$ref'].split('/')[-1]
                    references.append(ref_name)
                elif prop.get('type') == 'object':
                    # Nested object, process it
                    type_name = self.get_type_name_for_schema(prop)
                    prop['$ref'] = f"#/components/schemas/{type_name}"
                    references.append(type_name)
                    self.global_types[type_name] = prop
                    self.type_names.add(type_name)
                    self.collect_nested_types(prop)
                elif prop.get('type') == 'array':
                    items = prop.get('items', {})
                    if '$ref' in items:
                        ref_name = items['$ref'].split('/')[-1]
                        references.append(ref_name)
                    elif items.get('type') == 'object':
                        # Nested object in array, process it
                        type_name = self.get_type_name_for_schema(items)
                        items['$ref'] = f"#/components/schemas/{type_name}"
                        references.append(type_name)
                        self.global_types[type_name] = items
                        self.type_names.add(type_name)
                        self.collect_nested_types(items)
        return properties, required_fields, references

    def get_type_name_for_schema(self, schema):
        """Get or assign a type name for a schema."""
        if 'title' in schema:
            type_name = schema['title']
        else:
            self.anonymous_type_count += 1
            type_name = f"AnonymousType_{self.anonymous_type_count}"
        while type_name in self.type_names:
            self.anonymous_type_count += 1
            type_name = f"AnonymousType_{self.anonymous_type_count}"
        return type_name

    def process_properties(self, properties, required_fields, references):
        """Create XSD complexType elements based on OpenAPI properties and references."""
        complex_type = ET.Element('xs:complexType')
        sequence_elem = ET.SubElement(complex_type, 'xs:sequence')

        # Add referenced elements first (for allOf $ref scenarios)
        for ref_name in references:
            sequence_elem.append(ET.Element('xs:element', ref=ref_name))

        for prop_name, prop_details in properties.items():
            if 'allOf' in prop_details:
                merged_props, merged_reqs, merged_refs = self.merge_all_of_schemas(prop_details['allOf'])
                nested_complex_type = self.process_properties(merged_props, merged_reqs, merged_refs)
                sequence_elem.append(self.create_xsd_element(prop_name, complex_type=nested_complex_type, required=True))
            elif 'anyOf' in prop_details:
                self.process_any_of(prop_name, prop_details, sequence_elem)
            elif '$ref' in prop_details:
                ref_name = prop_details['$ref'].split('/')[-1]
                element = self.create_xsd_element(prop_name, ref=ref_name, required=(prop_name in required_fields))
                sequence_elem.append(element)
            else:
                self.process_simple_type(prop_name, prop_details, sequence_elem, required_fields)

        return complex_type

    def process_any_of(self, prop_name, prop_details, sequence_elem):
        """Process anyOf construct in OpenAPI properties."""
        choice_element = ET.Element('xs:choice')
        for option in prop_details['anyOf']:
            if '$ref' in option:
                ref_name = option['$ref'].split('/')[-1]
                choice_element.append(ET.Element('xs:element', ref=ref_name))
            else:
                yaml_type = option.get('type', 'string')
                xsd_type = self.yaml_type_to_xsd_type(yaml_type)
                choice_element.append(ET.Element('xs:element', type=xsd_type))

        sequence_elem.append(self.create_xsd_element(prop_name, complex_type=choice_element, required=True))

    def process_simple_type(self, prop_name, prop_details, sequence_elem, required_fields):
        """Process simple types, enums, and arrays in OpenAPI properties."""
        yaml_type = prop_details.get('type', 'string')
        is_required = prop_name in required_fields

        if yaml_type == 'array':
            items = prop_details.get('items', {})
            if '$ref' in items:
                ref_name = items['$ref'].split('/')[-1]
                element = self.create_xsd_element(prop_name, ref=ref_name, required=is_required, is_array=True)
                sequence_elem.append(element)
            elif items.get('type') == 'object':
                # Process the object and create a global type
                type_name = self.get_type_name_for_schema(items)
                items['$ref'] = f"#/components/schemas/{type_name}"
                self.global_types[type_name] = items
                self.type_names.add(type_name)
                self.collect_nested_types(items)
                element = self.create_xsd_element(prop_name, ref=type_name, required=is_required, is_array=True)
                sequence_elem.append(element)
            elif items.get('type') == 'string' and 'enum' in items:
                enum_values = items['enum']
                simple_type = self.create_enum_restriction('xs:string', enum_values)
                sequence_elem.append(self.create_xsd_element(
                    prop_name, is_array=True, required=is_required, complex_type=simple_type))
            else:
                item_type = self.yaml_type_to_xsd_type(items.get('type', 'string'))
                sequence_elem.append(self.create_xsd_element(
                    prop_name, element_type=item_type, required=is_required, is_array=True))
        elif yaml_type == 'object':
            if '$ref' in prop_details:
                ref_name = prop_details['$ref'].split('/')[-1]
                element = self.create_xsd_element(prop_name, ref=ref_name, required=is_required)
                sequence_elem.append(element)
            else:
                # Process the object and create a global type
                type_name = self.get_type_name_for_schema(prop_details)
                prop_details['$ref'] = f"#/components/schemas/{type_name}"
                self.global_types[type_name] = prop_details
                self.type_names.add(type_name)
                self.collect_nested_types(prop_details)
                element = self.create_xsd_element(prop_name, ref=type_name, required=is_required)
                sequence_elem.append(element)
        elif yaml_type == 'string' and 'enum' in prop_details:
            enum_values = prop_details['enum']
            sequence_elem.append(self.create_xsd_element(
                prop_name, element_type='xs:string', required=is_required, enum_values=enum_values))
        else:
            xsd_type = self.yaml_type_to_xsd_type(yaml_type)
            sequence_elem.append(self.create_xsd_element(
                prop_name, element_type=xsd_type, required=is_required))

    def generate_global_xsd_types(self, root, exclude_types):
        """Generate global types for each schema, excluding specified types."""
        # Ensure all types are collected
        self.collect_all_types()

        for type_name, schema_details in self.global_types.items():
            if type_name in exclude_types:
                continue  # Skip excluded types

            if schema_details.get('type') == 'string' and 'enum' in schema_details:
                # Create a global enum type
                simple_type = ET.SubElement(root, 'xs:simpleType', name=type_name)
                enum_restriction = self.create_enum_restriction('xs:string', schema_details['enum'])
                simple_type.append(enum_restriction)
            elif schema_details.get('type') == 'object' or 'properties' in schema_details:
                # Create a global complex type
                complex_type_elem = ET.SubElement(root, 'xs:complexType', name=type_name)
                properties = schema_details.get('properties', {})
                required_fields = schema_details.get('required', [])
                references = []

                if 'allOf' in schema_details:
                    properties, required_fields, references = self.merge_all_of_schemas(schema_details['allOf'])
                else:
                    # Collect references from properties
                    for prop in properties.values():
                        if '$ref' in prop:
                            ref_name = prop['$ref'].split('/')[-1]
                            references.append(ref_name)

                processed_complex_type = self.process_properties(properties, required_fields, references)
                complex_type_elem.extend(processed_complex_type)
            else:
                # Handle other types as simple types
                xsd_type = self.yaml_type_to_xsd_type(schema_details.get('type', 'string'))
                ET.SubElement(root, 'xs:simpleType', name=type_name, base=xsd_type)

    def generate_xsd(self, output_stream, exclude_request_body_types, exclude_list):
        """Generate an XML Schema (XSD) from an OpenAPI YAML specification."""
        if exclude_request_body_types:
            request_body_only_types = self.find_request_body_only_types()
        else:
            request_body_only_types = set()
        exclude_types = request_body_only_types.union(exclude_list)
        root = ET.Element('xs:schema', attrib={
            'xmlns:xs': "http://www.w3.org/2001/XMLSchema",
            'elementFormDefault': "qualified"
        })

        # Generate global types
        self.generate_global_xsd_types(root, exclude_types)

        # Generate main elements
        for type_name in self.global_types.keys():
            if type_name not in exclude_types:
                ET.SubElement(root, 'xs:element', name=type_name, type=type_name)

        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ", level=0)
        output_stream.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(output_stream, encoding='unicode', method="xml")

def load_openapi_from_file_or_stdin(input_file):
    """Load OpenAPI specification from a file or stdin."""
    if input_file:
        with open(input_file, 'r') as file:
            return yaml.safe_load(file)
    else:
        return yaml.safe_load(sys.stdin.read())

def load_exclude_list(exclude_input):
    """Load the exclude list from a file or a comma-separated list."""
    if os.path.isfile(exclude_input):
        with open(exclude_input, 'r') as file:
            return set(line.strip() for line in file if line.strip())
    else:
        return set(exclude_input.split(','))

def main():
    parser = argparse.ArgumentParser(description='Convert OpenAPI to XSD.')
    parser.add_argument('-i', '--input', help='Input file containing OpenAPI specification (defaults to stdin)')
    parser.add_argument('-o', '--output', help='Output file for XSD schema (defaults to stdout)')
    parser.add_argument('--exclude-request-body-types', action='store_true',
                        help='Exclude types used only in request bodies from the XSD')
    parser.add_argument('--exclude',
                        help='Comma-separated list or file of object names to exclude from the schema')
    args = parser.parse_args()

    # Load OpenAPI specification
    openapi_spec = load_openapi_from_file_or_stdin(args.input)

    # Load the exclude list
    exclude_list = load_exclude_list(args.exclude) if args.exclude else set()

    # Create converter instance
    converter = OpenAPIToXSDConverter(openapi_spec)

    # Generate XSD
    if args.output:
        with open(args.output, 'w') as output_file:
            converter.generate_xsd(output_file, args.exclude_request_body_types, exclude_list)
    else:
        converter.generate_xsd(sys.stdout, args.exclude_request_body_types, exclude_list)

if __name__ == "__main__":
    main()
