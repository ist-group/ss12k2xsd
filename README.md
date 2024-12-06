# Project Title

Generates an XML schema from an OpenAPI specification

## Description

The intension of this tool is to help creating an XML version of the SS12000 specification. The existing specifications is available in OpenAPI yaml form, hence the tool use that as input. Simply use a pipe to feed it and the result is coming out the other end. The tool also supports OpenAPI specifications in json format.

## Getting Started

### Dependencies

* Python 3
* PyYAML

### Installing

```bash
python3 -m venv .env
pip install -r requirements.txt
source .env/bin/activate
```

### Executing program

```bash
usage: oas2xsd.py [-h] [-i INPUT] [-o OUTPUT] [--exclude-request-body-types] [--exclude EXCLUDE]

Convert OpenAPI to XSD.

options:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        Input file containing OpenAPI specification (defaults to stdin)
  -o OUTPUT, --output OUTPUT
                        Output file for XSD schema (defaults to stdout)
  --exclude-request-body-types
                        Exclude types used only in request bodies from the XSD
  --include INCLUDE     If provided, only those listed types are included in the schema, overriding any request-body-only or exclude logic.
  --exclude EXCLUDE     Comma-separated list of object names or a file containing object names to exclude from the schema
  --expand inlines the specified types wherever they are referenced, instead of referencing them by type.
```

Example:

```bash
python script.py -i openapi_ss12000_version2_1_0.yaml -o openapi.xsd --include include_list.txt --expand expand_list.txt
```

## Version History

* 0.1
  * Initial Release

## License

This project is licensed under the MIT License - see the LICENSE.md file for details

## Acknowledgments

* [ChatGPT](https://chatgpt.com)
