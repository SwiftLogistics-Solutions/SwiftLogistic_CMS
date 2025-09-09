from flask import Flask, request, Response
import uuid
import xml.etree.ElementTree as ET

app = Flask(__name__)
package_store = {}

def create_soap_response(body_content):
    """Helper to create SOAP response"""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
    <soap:Body>
        {body_content}
    </soap:Body>
</soap:Envelope>"""

def extract_text_by_tag_name(root, tag_name):
    """Helper function to extract text from XML element by tag name"""
    for elem in root.iter():
        if elem.tag.endswith(tag_name):
            return elem.text
    return None

@app.route('/orderService', methods=['POST'])
def soap_service():
    try:
        # Parse incoming SOAP request
        root = ET.fromstring(request.data)
        
        # Find the method being called
        for elem in root.iter():
            if elem.tag.endswith('new_package'):
                new_id = str(uuid.uuid4())
                package_store[new_id] = "Awaiting Packing"
                print(f"Created new package with ID: {new_id}")
                
                response_body = f'<new_package_response>{new_id}</new_package_response>'
                return Response(create_soap_response(response_body), 
                              content_type='text/xml')
                
            elif elem.tag.endswith('update_package'):
                package_id = extract_text_by_tag_name(root, 'package_id')
                status_code = extract_text_by_tag_name(root, 'status_code')
                
                if not package_id or not status_code:
                    result = "Error: Missing package_id or status_code"
                elif package_id not in package_store:
                    result = "Error: Package not found"
                else:
                    package_store[package_id] = status_code
                    result = "Success"
                    print(f"Updated package {package_id} to status: {status_code}")
                
                response_body = f'<update_package_response>{result}</update_package_response>'
                return Response(create_soap_response(response_body), 
                              content_type='text/xml')
                
            elif elem.tag.endswith('get_package_status'):
                package_id = elem.find('.//*[local-name()="package_id"]').text
                
                if package_id not in package_store:
                    result = "Error: Package not found"
                else:
                    result = package_store[package_id]
                    print(f"Retrieved status for package {package_id}: {result}")
                
                response_body = f'<get_package_status_response>{result}</get_package_status_response>'
                return Response(create_soap_response(response_body), 
                              content_type='text/xml')
        
        return Response("Method not found", status=400)
        
    except Exception as e:
        return Response(f"Error: {str(e)}", status=500)

@app.route('/orderService', methods=['GET'])
def wsdl():
    if request.args.get('wsdl') is not None:
        # Return a simple WSDL (you'd want a more complete one in production)
        wsdl_content = """<?xml version="1.0" encoding="utf-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/">
    <!-- WSDL content would go here -->
    <message name="Simple WSDL - implement full WSDL as needed"/>
</definitions>"""
        return Response(wsdl_content, content_type='text/xml')
    return "SOAP Service"

if __name__ == '__main__':
    print("WMS SOAP server listening on http://127.0.0.1:8000")
    print("Service endpoint: http://127.0.0.1:8000/orderService")
    print("WSDL location: http://127.0.0.1:8000/orderService?wsdl")
    
    app.run(host='127.0.0.1', port=8000, debug=True)