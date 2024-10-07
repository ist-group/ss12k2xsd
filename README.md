# Project Title

Generates an XML schema from an OpenAPI specification

## Description

The intension of this tool is to help creating an XML version of the SS12000 specification. The existing specifications is available in OpenAPI yaml form, hence the tool use that as input. Simply use a pipe to feed it and the result is coming out the other end. The tool also supports OpenAPI specifications in json format.

## Getting Started

### Dependencies

* Python 3
* PyYAML

### Installing

* Install or activate Python 3 environment
* pip install -r requirements.txt

### Executing program

```bash
cat openapi_ss12000_version2_1_0.yaml | python3 oas2xsd.py > ss12000_version2_1.0.xsd
```

## Version History

* 0.1
  * Initial Release

## License

This project is licensed under the MIT License - see the LICENSE.md file for details

## Acknowledgments

* [ChatGPT](https://chatgpt.com)
