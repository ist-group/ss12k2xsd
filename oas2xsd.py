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

def create_xsd_element(element_name, element_type=None, required=False, is_array=False, complex_type=None):
    """Create an XML Schema element."""
    elem = ET.Element('xs:element', name=element_name)
    
    if is_array:
        elem.set("maxOccurs", "unbounded")
        
    if required:
        elem.set("minOccurs", "1")
    else:
        elem.set("minOccurs", "0")
    
    # Use the complexType if provided, otherwise set the type
    if complex_type is not None:
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

def process_properties(properties, required_fields, openapi_spec):
    """Create XSD complexType elements based on OpenAPI properties."""
    complex_type = ET.Element('xs:complexType')
    sequence = ET.SubElement(complex_type, 'xs:sequence')
    
    for prop_name, prop_details in properties.items():
        # Handle the case where the property is a reference
        if '$ref' in prop_details:
            ref_schema = resolve_ref(prop_details['$ref'], openapi_spec)
            nested_complex_type = process_properties(ref_schema.get('properties', {}), ref_schema.get('required', []), openapi_spec)
            sequence.append(create_xsd_element(prop_name, complex_type=nested_complex_type, required=True))
        else:
            yaml_type = prop_details.get('type', 'string')
            is_required = prop_name in required_fields
            is_array = yaml_type == "array"

            # Handle array types with nested items
            if is_array:
                items = prop_details.get('items', {})
                if '$ref' in items:
                    ref_schema = resolve_ref(items['$ref'], openapi_spec)
                    nested_complex_type = process_properties(ref_schema.get('properties', {}), ref_schema.get('required', []), openapi_spec)
                    sequence.append(create_xsd_element(prop_name, is_array=True, required=is_required, complex_type=nested_complex_type))
                else:
                    item_type = yaml_type_to_xsd_type(items.get('type', 'string'))
                    sequence.append(create_xsd_element(prop_name, element_type=item_type, required=is_required, is_array=True))

            # Handle nested objects
            elif yaml_type == 'object':
                nested_complex_type = process_properties(prop_details.get('properties', {}), prop_details.get('required', []), openapi_spec)
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
