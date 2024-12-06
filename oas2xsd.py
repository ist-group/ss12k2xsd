#!/usr/bin/env python

import sys
import yaml
import xml.etree.ElementTree as ET
import argparse
import os

def yaml_type_to_xsd_type(yaml_type):
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
    restriction = ET.Element('xs:restriction', base=element_type)
    for value in enum_values:
        ET.SubElement(restriction, 'xs:enumeration', value=value)
    simple_type = ET.Element('xs:simpleType')
    simple_type.append(restriction)
    return simple_type

def find_request_body_only_types(openapi_spec):
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
    if '$ref' in schema:
        ref_schema = resolve_ref(schema['$ref'], openapi_spec)
        if 'allOf' in ref_schema:
            return merge_all_of_schemas(ref_schema['allOf'], openapi_spec)
        return ref_schema.get('properties', {}), ref_schema.get('required', [])
    return schema.get('properties', {}), schema.get('required', [])

def load_list_from_input(input_value):
    if input_value is None:
        return set()
    if os.path.isfile(input_value):
        with open(input_value, 'r') as file:
            return set(line.strip() for line in file if line.strip())
    else:
        return set(input_value.split(','))

def load_openapi_from_file_or_stdin(input_file):
    if input_file:
        with open(input_file, 'r') as file:
            return yaml.safe_load(file)
    else:
        return yaml.safe_load(sys.stdin.read())

def inline_schema(schema_name, openapi_spec, expand_list):
    schemas = openapi_spec.get('components', {}).get('schemas', {})
    schema_details = schemas.get(schema_name, {})
    schema_type = schema_details.get('type', 'object')

    if schema_type == 'string' and 'enum' in schema_details:
        return create_enum_restriction('xs:string', schema_details['enum'])
    elif schema_type == 'object':
        properties = schema_details.get('properties', {})
        required = schema_details.get('required', [])
        merged_properties, merged_required, merged_references = merge_all_of_schemas(schema_details.get('allOf', []), openapi_spec)
        properties.update(merged_properties)
        required = list(set(required + merged_required))
        return process_properties(properties, required, merged_references, openapi_spec, expand_list)
    elif schema_type == 'array':
        array_complex = ET.Element('xs:complexType')
        seq = ET.SubElement(array_complex, 'xs:sequence')
        items = schema_details.get('items', {})
        if '$ref' in items:
            ref_name = items['$ref'].split('/')[-1]
            if ref_name in expand_list:
                inlined = inline_schema(ref_name, openapi_spec, expand_list)
                item_elem = ET.Element('xs:element', name="item", minOccurs="1", maxOccurs="unbounded")
                item_elem.append(inlined)
                seq.append(item_elem)
            else:
                seq.append(ET.Element('xs:element', name="item", type=ref_name, minOccurs="1", maxOccurs="unbounded"))
        else:
            item_type = items.get('type', 'string')
            if item_type == 'string' and 'enum' in items:
                enum_elem = create_enum_restriction('xs:string', items['enum'])
                item_elem = ET.Element('xs:element', name="item", minOccurs="1", maxOccurs="unbounded")
                item_elem.append(enum_elem)
                seq.append(item_elem)
            elif item_type == 'object':
                item_properties = items.get('properties', {})
                item_required = items.get('required', [])
                _, _, item_refs = merge_all_of_schemas(items.get('allOf', []), openapi_spec)
                item_complex = process_properties(item_properties, item_required, item_refs, openapi_spec, expand_list)
                item_elem = ET.Element('xs:element', name="item", minOccurs="1", maxOccurs="unbounded")
                item_elem.append(item_complex)
                seq.append(item_elem)
            else:
                xsd_type = yaml_type_to_xsd_type(item_type)
                seq.append(ET.Element('xs:element', name="item", type=xsd_type, minOccurs="1", maxOccurs="unbounded"))
        return array_complex
    else:
        # simple non-enum type
        base_type = yaml_type_to_xsd_type(schema_type)
        st = ET.Element('xs:simpleType')
        ET.SubElement(st, 'xs:restriction', base=base_type)
        return st

def create_xsd_element(element_name, element_type=None, required=False, is_array=False, complex_type=None, enum_values=None):
    elem = ET.Element('xs:element', name=element_name)
    elem.set("minOccurs", "1" if required else "0")
    if is_array:
        elem.set("maxOccurs", "unbounded")

    if enum_values:
        elem.append(create_enum_restriction(element_type, enum_values))
    elif complex_type is not None:
        elem.append(complex_type)
    else:
        elem.set('type', element_type)
    return elem

def resolve_ref(ref_path, openapi_spec):
    ref_parts = ref_path.strip('#/').split('/')
    ref_schema = openapi_spec
    for part in ref_parts:
        ref_schema = ref_schema.get(part, {})
    return ref_schema

def process_simple_type(prop_name, prop_details, sequence, required_fields, openapi_spec, expand_list):
    yaml_type = prop_details.get('type', 'string')
    is_required = prop_name in required_fields

    if yaml_type == 'string' and 'enum' in prop_details:
        enum_values = prop_details['enum']
        sequence.append(create_xsd_element(prop_name, required=is_required, element_type='xs:string', enum_values=enum_values))
    elif yaml_type == 'array':
        items = prop_details.get('items', {})
        if '$ref' in items:
            ref_name = items['$ref'].split('/')[-1]
            if ref_name in expand_list:
                inlined = inline_schema(ref_name, openapi_spec, expand_list)
                item_elem = ET.Element('xs:element', name=prop_name, minOccurs="1", maxOccurs="unbounded")
                item_elem.append(inlined)
                sequence.append(item_elem)
            else:
                sequence.append(ET.Element('xs:element', name=prop_name, type=ref_name, minOccurs="1", maxOccurs="unbounded"))
        else:
            item_type = items.get('type', 'string')
            if item_type == 'string' and 'enum' in items:
                enum_elem = create_enum_restriction('xs:string', items['enum'])
                item_elem = ET.Element('xs:element', name=prop_name, minOccurs="1", maxOccurs="unbounded")
                item_elem.append(enum_elem)
                sequence.append(item_elem)
            elif item_type == 'object':
                item_properties = items.get('properties', {})
                item_required = items.get('required', [])
                _, _, item_refs = merge_all_of_schemas(items.get('allOf', []), openapi_spec)
                item_complex = process_properties(item_properties, item_required, item_refs, openapi_spec, expand_list)
                item_elem = ET.Element('xs:element', name=prop_name, minOccurs="1", maxOccurs="unbounded")
                item_elem.append(item_complex)
                sequence.append(item_elem)
            else:
                xsd_type = yaml_type_to_xsd_type(item_type)
                sequence.append(ET.Element('xs:element', name=prop_name, type=xsd_type, minOccurs="1", maxOccurs="unbounded"))
    elif yaml_type == 'object':
        nested_properties = prop_details.get('properties', {})
        nested_required = prop_details.get('required', [])
        _, _, nested_refs = merge_all_of_schemas(prop_details.get('allOf', []), openapi_spec)
        nested_complex_type = process_properties(nested_properties, nested_required, nested_refs, openapi_spec, expand_list)
        sequence.append(create_xsd_element(prop_name, required=is_required, complex_type=nested_complex_type))
    else:
        xsd_type = yaml_type_to_xsd_type(yaml_type)
        sequence.append(create_xsd_element(prop_name, required=is_required, element_type=xsd_type))

def process_any_of(prop_name, prop_details, sequence, openapi_spec, expand_list):
    choice_element = ET.Element('xs:choice')
    anyof_options = prop_details['anyOf']
    for i, option in enumerate(anyof_options):
        option_element_name = f"{prop_name}_option{i}"
        if '$ref' in option:
            ref_name = option['$ref'].split('/')[-1]
            if ref_name in expand_list:
                inlined = inline_schema(ref_name, openapi_spec, expand_list)
                opt_elem = ET.Element('xs:element', name=option_element_name, minOccurs="1")
                opt_elem.append(inlined)
                choice_element.append(opt_elem)
            else:
                choice_element.append(ET.Element('xs:element', name=option_element_name, type=ref_name, minOccurs="1"))
        else:
            yaml_type = option.get('type', 'string')
            xsd_type = yaml_type_to_xsd_type(yaml_type)
            choice_element.append(ET.Element('xs:element', name=option_element_name, type=xsd_type, minOccurs="1"))
    elem = ET.Element('xs:element', name=prop_name, minOccurs="1")
    elem.append(choice_element)
    sequence.append(elem)

def process_properties(properties, required_fields, references, openapi_spec, expand_list):
    complex_type = ET.Element('xs:complexType')
    sequence = ET.SubElement(complex_type, 'xs:sequence')

    for prop_name, prop_details in properties.items():
        if 'allOf' in prop_details:
            merged_properties, merged_required, merged_references = merge_all_of_schemas(prop_details['allOf'], openapi_spec)
            merged_properties.update(prop_details.get('properties', {}))
            merged_required = list(set(merged_required + prop_details.get('required', [])))
            nested_complex_type = process_properties(merged_properties, merged_required, merged_references, openapi_spec, expand_list)
            sequence.append(create_xsd_element(prop_name, required=True, complex_type=nested_complex_type))
        elif 'anyOf' in prop_details:
            process_any_of(prop_name, prop_details, sequence, openapi_spec, expand_list)
        elif '$ref' in prop_details:
            ref_name = prop_details['$ref'].split('/')[-1]
            if ref_name in expand_list:
                # Inline
                inlined = inline_schema(ref_name, openapi_spec, expand_list)
                prop_elem = ET.Element('xs:element', name=prop_name, minOccurs="1")
                prop_elem.append(inlined)
                sequence.append(prop_elem)
            else:
                sequence.append(ET.Element('xs:element', name=prop_name, type=ref_name, minOccurs="1"))
        else:
            process_simple_type(prop_name, prop_details, sequence, required_fields, openapi_spec, expand_list)

    return complex_type

def generate_global_xsd_types(openapi_spec, root, exclude_types, include_types, expand_list):
    schemas = openapi_spec.get('components', {}).get('schemas', {})
    schema_names_to_process = include_types if include_types else schemas.keys()

    # global definitions are always not expanded here; expansions occur at usage time
    for schema_name in schema_names_to_process:
        if schema_name not in schemas:
            continue
        if schema_name in exclude_types:
            continue
        schema_details = schemas[schema_name]
        schema_type = schema_details.get('type', 'object')
        if schema_type == 'string' and 'enum' in schema_details:
            simple_type = ET.SubElement(root, 'xs:simpleType', name=schema_name)
            enum_restriction = create_enum_restriction('xs:string', schema_details['enum'])
            simple_type.append(enum_restriction)
        else:
            complex_type = ET.SubElement(root, 'xs:complexType', name=schema_name)
            properties = schema_details.get('properties', {})
            required_fields = schema_details.get('required', [])
            _, _, references = merge_all_of_schemas(schema_details.get('allOf', []), openapi_spec)
            processed_complex_type = process_properties(properties, required_fields, references, openapi_spec, expand_list)
            complex_type.extend(processed_complex_type)

def generate_xsd_from_openapi(openapi_spec, output_stream, exclude_request_body_types, exclude_list, include_list, expand_list):
    if include_list:
        exclude_types = set()
    else:
        request_body_only_types = find_request_body_only_types(openapi_spec) if exclude_request_body_types else set()
        exclude_types = request_body_only_types.union(exclude_list)

    root = ET.Element('xs:schema', xmlns_xs="http://www.w3.org/2001/XMLSchema", elementFormDefault="qualified")

    generate_global_xsd_types(openapi_spec, root, exclude_types, include_list, expand_list)

    schemas = openapi_spec.get('components', {}).get('schemas', {})
    if include_list:
        for schema_name in include_list:
            if schema_name in schemas and schema_name not in exclude_types:
                ET.SubElement(root, 'xs:element', name=schema_name, type=schema_name)
    else:
        for schema_name in schemas.keys():
            if schema_name not in exclude_types:
                ET.SubElement(root, 'xs:element', name=schema_name, type=schema_name)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    output_stream.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(output_stream, encoding='unicode', method="xml")

def main():
    parser = argparse.ArgumentParser(description='Convert OpenAPI to XSD with optional expansions.')
    parser.add_argument('-i', '--input', help='Input OpenAPI file (defaults to stdin)')
    parser.add_argument('-o', '--output', help='Output XSD file (defaults to stdout)')
    parser.add_argument('--exclude-request-body-types', action='store_true', help='Exclude types used only in request bodies')
    parser.add_argument('--exclude', help='Comma-separated list or file of object names to exclude')
    parser.add_argument('--include', help='Comma-separated list or file of object names to include (overrides exclude logic)')
    parser.add_argument('--expand', help='Comma-separated list or file of object names to expand inline')
    args = parser.parse_args()

    openapi_spec = load_openapi_from_file_or_stdin(args.input)
    exclude_list = load_list_from_input(args.exclude)
    include_list = load_list_from_input(args.include)
    expand_list = load_list_from_input(args.expand)

    if args.output:
        with open(args.output, 'w') as output_file:
            generate_xsd_from_openapi(openapi_spec, output_file, args.exclude_request_body_types, exclude_list, include_list, expand_list)
    else:
        generate_xsd_from_openapi(openapi_spec, sys.stdout, args.exclude_request_body_types, exclude_list, include_list, expand_list)

if __name__ == "__main__":
    main()