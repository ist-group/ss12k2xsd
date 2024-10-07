import sys
import yaml
import xml.etree.ElementTree as ET

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

def create_xsd_element(element_name, element_type=None, required=False, is_array=False, complex_type=None, enum_values=None):
    """Create an XML Schema element."""
    elem = ET.Element('xs:element', name=element_name)
    
    if is_array:
        elem.set("maxOccurs", "unbounded")
        
    if required:
        elem.set("minOccurs", "1")
    else:
        elem.set("minOccurs", "0")

    if enum_values:
        restriction = ET.Element('xs:restriction', base=element_type)
        for value in enum_values:
            ET.SubElement(restriction, 'xs:enumeration', value=value)
        simple_type = ET.Element('xs:simpleType')
        simple_type.append(restriction)
        elem.append(simple_type)
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

def merge_all_of_schemas(all_of_list, openapi_spec):
    """Merge multiple schemas defined in an allOf list into a single schema."""
    merged_properties = {}
    required_fields = []
    for schema in all_of_list:
        if '$ref' in schema:
            ref_schema = resolve_ref(schema['$ref'], openapi_spec)
            properties = ref_schema.get('properties', {})
            required = ref_schema.get('required', [])
        else:
            properties = schema.get('properties', {})
            required = schema.get('required', [])
        merged_properties.update(properties)
        required_fields.extend(required)
    return merged_properties, list(set(required_fields))  # Remove duplicates from required fields

def process_properties(properties, required_fields, openapi_spec):
    """Create XSD complexType elements based on OpenAPI properties, handling multiple levels of nested objects."""
    complex_type = ET.Element('xs:complexType')
    sequence = ET.SubElement(complex_type, 'xs:sequence')
    
    for prop_name, prop_details in properties.items():
        # Handle allOf construct
        if 'allOf' in prop_details:
            merged_properties, merged_required = merge_all_of_schemas(prop_details['allOf'], openapi_spec)
            nested_complex_type = process_properties(merged_properties, merged_required, openapi_spec)
            sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=True))

        # Handle anyOf construct by creating a choice element
        elif 'anyOf' in prop_details:
            choice_type = ET.Element('xs:choice')
            for option in prop_details['anyOf']:
                if '$ref' in option:
                    ref_schema = resolve_ref(option['$ref'], openapi_spec)
                    nested_complex_type = process_properties(ref_schema.get('properties', {}), ref_schema.get('required', []), openapi_spec)
                    choice_type.append(ET.Element('xs:element', type="xs:anyType"))
                else:
                    yaml_type = option.get('type', 'string')
                    xsd_type = yaml_type_to_xsd_type(yaml_type)
                    choice_type.append(ET.Element('xs:element', type=xsd_type))
            sequence.append(create_xsd_element(prop_name, complex_type=choice_type, required=True))

        # Handle the case where the property is a reference
        elif '$ref' in prop_details:
            ref_schema = resolve_ref(prop_details['$ref'], openapi_spec)
            nested_complex_type = process_properties(ref_schema.get('properties', {}), ref_schema.get('required', []), openapi_spec)
            sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=True))

        else:
            yaml_type = prop_details.get('type', 'string')
            is_required = prop_name in required_fields
            is_array = yaml_type == "array"

            # Handle enum values for strings
            if yaml_type == 'string' and 'enum' in prop_details:
                enum_values = prop_details['enum']
                sequence.append(create_xsd_element(prop_name, element_type='xs:string', required=is_required, enum_values=enum_values))

            # Handle array types with nested items, including objects
            elif is_array:
                items = prop_details.get('items', {})
                if '$ref' in items:
                    ref_schema = resolve_ref(items['$ref'], openapi_spec)
                    nested_complex_type = process_properties(ref_schema.get('properties', {}), ref_schema.get('required', []), openapi_spec)
                    sequence.append(create_xsd_element(prop_name, is_array=True, required=is_required, complex_type=nested_complex_type))
                elif items.get('type') == 'object':
                    nested_complex_type = process_properties(items.get('properties', {}), items.get('required', []), openapi_spec)
                    sequence.append(create_xsd_element(prop_name, is_array=True, required=is_required, complex_type=nested_complex_type))
                else:
                    item_type = yaml_type_to_xsd_type(items.get('type', 'string'))
                    sequence.append(create_xsd_element(prop_name, element_type=item_type, required=is_required, is_array=True))

            # Handle nested objects directly within the current schema
            elif yaml_type == 'object':
                nested_properties = prop_details.get('properties', {})
                nested_required = prop_details.get('required', [])
                nested_complex_type = process_properties(nested_properties, nested_required, openapi_spec)
                sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=is_required))

            # Handle simple types
            else:
                xsd_type = yaml_type_to_xsd_type(yaml_type)
                sequence.append(create_xsd_element(prop_name, element_type=xsd_type, required=is_required))
    
    return complex_type

def generate_xsd_from_openapi(openapi_spec):
    """Generate an XML Schema (XSD) from an OpenAPI YAML specification."""
    root = ET.Element('xs:schema', xmlns_xs="http://www.w3.org/2001/XMLSchema", elementFormDefault="qualified")
    
    # Iterate through the OpenAPI components/schemas
    schemas = openapi_spec.get('components', {}).get('schemas', {})
    for schema_name, schema_details in schemas.items():
        element = ET.SubElement(root, 'xs:element', name=schema_name)
        properties = schema_details.get('properties', {})
        required_fields = schema_details.get('required', [])
        
        # Create complexType for the schema
        complex_type = process_properties(properties, required_fields, openapi_spec)
        element.append(complex_type)
    
    # Pretty print the XML tree
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    
    # Output to stdout
    sys.stdout.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(sys.stdout, encoding='unicode', method="xml")

# Load OpenAPI spec from stdin
def load_openapi_from_stdin():
    """Load OpenAPI specification from stdin."""
    openapi_spec = yaml.safe_load(sys.stdin.read())
    return openapi_spec

# Main script to load OpenAPI YAML from stdin and generate XSD to stdout
if __name__ == "__main__":
    # Load the YAML OpenAPI spec from stdin
    openapi_spec = load_openapi_from_stdin()
    
    # Generate the XSD and print to stdout
    generate_xsd_from_openapi(openapi_spec)
