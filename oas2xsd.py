#!/usr/bin/env python

import sys
import yaml
import xml.etree.ElementTree as ET
import argparse
import os

def yaml_type_to_xsd_type(yaml_type):
    """Map OpenAPI YAML types to XSD types."""
    mapping = {
        "string": "xs:string",
        "integer": "xs:int",
        "boolean": "xs:boolean",
        "number": "xs:decimal",
        "array": "xs:sequence",
        "object": "xs:complexType"
    }
    return mapping.get(yaml_type, "xs:string")

def create_enum_restriction(element_type, enum_values):
    """Create an XSD simpleType with a restriction for enum values."""
    restriction = ET.Element('xs:restriction', base=element_type)
    for value in enum_values:
        ET.SubElement(restriction, 'xs:enumeration', value=value)
    simple_type = ET.Element('xs:simpleType')
    simple_type.append(restriction)
    return simple_type

def create_xsd_element(element_name, element_type=None, required=False, is_array=False, complex_type=None, enum_values=None, ref=None):
    """Create an XML Schema element."""
    elem = ET.Element('xs:element', name=element_name)
    elem.set("minOccurs", "1" if required else "0")
    if is_array:
        elem.set("maxOccurs", "unbounded")

    if ref:
        elem.set('ref', ref)
    elif enum_values:
        elem.append(create_enum_restriction(element_type, enum_values))
    elif complex_type is not None:
        elem.append(complex_type)
    else:
        elem.set('type', element_type)
    
    return elem

def resolve_ref(ref_path, openapi_spec):
    """Resolve a $ref path to its corresponding schema definition in the OpenAPI spec."""
    ref_parts = ref_path.strip('#/').split('/')
    ref_schema = openapi_spec
    for part in ref_parts:
        ref_schema = ref_schema.get(part, {})
    return ref_schema

def find_request_body_only_types(openapi_spec):
    """Find types used exclusively in request bodies."""
    request_body_types = set()
    schemas = openapi_spec.get('components', {}).get('schemas', {}).keys()

    paths = openapi_spec.get('paths', {})
    for path_data in paths.values():
        for method_data in path_data.values():
            request_body = method_data.get('requestBody', {})
            content = request_body.get('content', {})
            for media_type_data in content.values():
                schema_ref = media_type_data.get('schema', {}).get('$ref')
                if schema_ref:
                    schema_name = schema_ref.split('/')[-1]
                    if schema_name in schemas:
                        request_body_types.add(schema_name)

    return request_body_types

def merge_all_of_schemas(all_of_list, openapi_spec):
    """Merge multiple schemas defined in allOf into a single schema."""
    merged_properties = {}
    required_fields = []
    references = []

    for schema in all_of_list:
        if '$ref' in schema:
            ref_name = schema['$ref'].split('/')[-1]
            references.append(ref_name)
        else:
            ref_properties, ref_required = process_ref_or_schema(schema, openapi_spec)
            merged_properties.update(ref_properties)
            required_fields.extend(ref_required)

    return merged_properties, list(set(required_fields)), references

def process_ref_or_schema(schema, openapi_spec):
    """Process a schema or $ref, returning properties and required fields."""
    if '$ref' in schema:
        ref_schema = resolve_ref(schema['$ref'], openapi_spec)
        if 'allOf' in ref_schema:
            return merge_all_of_schemas(ref_schema['allOf'], openapi_spec)
        return ref_schema.get('properties', {}), ref_schema.get('required', [])
    return schema.get('properties', {}), schema.get('required', [])

def process_properties(properties, required_fields, references, openapi_spec):
    """Create XSD complexType elements based on properties and references."""
    complex_type = ET.Element('xs:complexType')
    sequence = ET.SubElement(complex_type, 'xs:sequence')

    # Handle referenced elements from allOf
    for ref_name in references:
        sequence.append(ET.Element('xs:element', ref=ref_name))

    for prop_name, prop_details in properties.items():
        if 'allOf' in prop_details:
            merged_properties, merged_required, merged_references = merge_all_of_schemas(prop_details['allOf'], openapi_spec)
            nested_complex_type = process_properties(merged_properties, merged_required, merged_references, openapi_spec)
            sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=True))
        elif 'anyOf' in prop_details:
            process_any_of(prop_name, prop_details, sequence, openapi_spec)
        elif '$ref' in prop_details:
            ref_name = prop_details['$ref'].split('/')[-1]
            sequence.append(ET.Element('xs:element', ref=ref_name))
        else:
            process_simple_type(prop_name, prop_details, sequence, required_fields, openapi_spec)

    return complex_type

def process_any_of(prop_name, prop_details, sequence, openapi_spec):
    """Process anyOf construct in OpenAPI properties."""
    choice_element = ET.Element('xs:choice')
    for option in prop_details['anyOf']:
        if '$ref' in option:
            ref_name = option['$ref'].split('/')[-1]
            choice_element.append(ET.Element('xs:element', ref=ref_name))
        else:
            yaml_type = option.get('type', 'string')
            xsd_type = yaml_type_to_xsd_type(yaml_type)
            choice_element.append(ET.Element('xs:element', type=xsd_type))
    sequence.append(create_xsd_element(prop_name, complex_type=choice_element, required=True))

def process_simple_type(prop_name, prop_details, sequence, required_fields, openapi_spec):
    """Process simple types, enums, and arrays in properties."""
    yaml_type = prop_details.get('type', 'string')
    is_required = prop_name in required_fields
    is_array = yaml_type == "array"

    if yaml_type == 'string' and 'enum' in prop_details:
        enum_values = prop_details['enum']
        sequence.append(create_xsd_element(prop_name, element_type='xs:string', required=is_required, enum_values=enum_values))
    elif is_array:
        items = prop_details.get('items', {})
        if items.get('type') == 'string' and 'enum' in items:
            enum_values = items['enum']
            simple_type = create_enum_restriction('xs:string', enum_values)
            sequence.append(create_xsd_element(prop_name, is_array=True, required=is_required, complex_type=simple_type))
        elif '$ref' in items:
            ref_name = items['$ref'].split('/')[-1]
            sequence.append(ET.Element('xs:element', ref=ref_name, maxOccurs="unbounded"))
    elif yaml_type == 'object':
        nested_properties = prop_details.get('properties', {})
        nested_required = prop_details.get('required', [])
        nested_complex_type = process_properties(nested_properties, nested_required, [], openapi_spec)
        sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=is_required))
    else:
        xsd_type = yaml_type_to_xsd_type(yaml_type)
        sequence.append(create_xsd_element(prop_name, element_type=xsd_type, required=is_required))

def generate_global_xsd_types(openapi_spec, root, exclude_types, include_types):
    """Generate global types, factoring in exclude and include sets."""
    schemas = openapi_spec.get('components', {}).get('schemas', {})

    # If include_types is specified, only process those in include_types
    schema_names_to_process = include_types if include_types else schemas.keys()

    for schema_name in schema_names_to_process:
        if schema_name not in schemas:
            continue  # Type not defined in schemas
        if schema_name in exclude_types:
            continue

        schema_details = schemas[schema_name]
        if schema_details.get('type') == 'string' and 'enum' in schema_details:
            # Create a global enum type
            simple_type = ET.SubElement(root, 'xs:simpleType', name=schema_name)
            enum_restriction = create_enum_restriction('xs:string', schema_details['enum'])
            simple_type.append(enum_restriction)
        else:
            # Create a global complex type
            complex_type = ET.SubElement(root, 'xs:complexType', name=schema_name)
            properties = schema_details.get('properties', {})
            required_fields = schema_details.get('required', [])
            _, _, references = merge_all_of_schemas(schema_details.get('allOf', []), openapi_spec)
            processed_complex_type = process_properties(properties, required_fields, references, openapi_spec)
            complex_type.extend(processed_complex_type)

def generate_xsd_from_openapi(openapi_spec, output_stream, exclude_request_body_types, exclude_list, include_list):
    """Generate an XML Schema (XSD) from an OpenAPI YAML specification."""
    if include_list:
        # If include is specified, only generate those included types, ignoring request-body and exclude logic
        exclude_types = set()
        request_body_only_types = set()
    else:
        request_body_only_types = find_request_body_only_types(openapi_spec) if exclude_request_body_types else set()
        exclude_types = request_body_only_types.union(exclude_list)

    root = ET.Element('xs:schema', xmlns_xs="http://www.w3.org/2001/XMLSchema", elementFormDefault="qualified")
    
    # Generate global types
    generate_global_xsd_types(openapi_spec, root, exclude_types, include_list)

    schemas = openapi_spec.get('components', {}).get('schemas', {})
    if include_list:
        # Only include listed types
        for schema_name in include_list:
            if schema_name in schemas and schema_name not in exclude_types:
                ET.SubElement(root, 'xs:element', name=schema_name, type=schema_name)
    else:
        # Include all schemas not excluded
        for schema_name in schemas.keys():
            if schema_name not in exclude_types:
                ET.SubElement(root, 'xs:element', name=schema_name, type=schema_name)

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

def load_list_from_input(input_value):
    """Load a list of objects from either a file or a comma-separated list."""
    if input_value is None:
        return set()
    if os.path.isfile(input_value):
        with open(input_value, 'r') as file:
            return set(line.strip() for line in file if line.strip())
    else:
        return set(input_value.split(','))

def main():
    parser = argparse.ArgumentParser(description='Convert OpenAPI to XSD.')
    parser.add_argument('-i', '--input', help='Input file containing OpenAPI specification (defaults to stdin)')
    parser.add_argument('-o', '--output', help='Output file for XSD schema (defaults to stdout)')
    parser.add_argument('--exclude-request-body-types', action='store_true', help='Exclude types used only in request bodies from the XSD')
    parser.add_argument('--exclude', help='Comma-separated list of object names or a file containing object names to exclude from the schema')
    parser.add_argument('--include', help='Comma-separated list of object names or a file containing object names to include in the schema. Overrides exclude logic.')
    args = parser.parse_args()

    # Load OpenAPI specification
    openapi_spec = load_openapi_from_file_or_stdin(args.input)

    # Load exclude and include sets
    exclude_list = load_list_from_input(args.exclude)
    include_list = load_list_from_input(args.include)

    # Open output file or use stdout
    if args.output:
        with open(args.output, 'w') as output_file:
            generate_xsd_from_openapi(openapi_spec, output_file, args.exclude_request_body_types, exclude_list, include_list)
    else:
        generate_xsd_from_openapi(openapi_spec, sys.stdout, args.exclude_request_body_types, exclude_list, include_list)

if __name__ == "__main__":
    main()
