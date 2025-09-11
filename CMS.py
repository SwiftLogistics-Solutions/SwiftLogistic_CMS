from flask import Flask, request, Response
import uuid
import xml.etree.ElementTree as ET
from pymongo import MongoClient
from datetime import datetime
import os

app = Flask(__name__)
package_store = {}

# MongoDB connection
MONGODB_URI = "mongodb+srv://middleware58_db_user:12345@cluster-1.6ci6iel.mongodb.net/"
DB_NAME = "CMS"
COLLECTION_NAME = "customers"
ORDERS_COLLECTION_NAME = "orders"

# Load district coordinates from XML file
DISTRICT_COORDINATES = {}

def load_district_coordinates():
    """Load district data from XML file"""
    global DISTRICT_COORDINATES
    try:
        xml_file_path = os.path.join(os.path.dirname(__file__), 'district_coordinates.xml')
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        
        for district in root.findall('district'):
            district_name = district.get('name')
            latitude = float(district.find('latitude').text)
            longitude = float(district.find('longitude').text)
            
            aliases = []
            aliases_element = district.find('aliases')
            if aliases_element is not None:
                for alias in aliases_element.findall('alias'):
                    aliases.append(alias.text.lower())
            
            DISTRICT_COORDINATES[district_name] = {
                'latitude': latitude,
                'longitude': longitude,
                'aliases': aliases
            }
        
        print(f"Loaded {len(DISTRICT_COORDINATES)} districts from XML file")
        
    except Exception as e:
        print(f"Failed to load district coordinates: {str(e)}")
        DISTRICT_COORDINATES = {}

# Load district data on startup
load_district_coordinates()

try:
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    customers_collection = db[COLLECTION_NAME]
    orders_collection = db[ORDERS_COLLECTION_NAME]
    print("Connected to MongoDB successfully!")
except Exception as e:
    print(f"Failed to connect to MongoDB: {str(e)}")
    client = None

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

def detect_district_from_address(address):
    """
    Detect district from address string and return coordinates
    Returns: dict with district info or None if not found
    """
    if not address:
        return None
    
    address_lower = address.lower().strip()
    
    # Check each district and its aliases
    for district_name, district_data in DISTRICT_COORDINATES.items():
        # Check main district name
        if district_name.lower() in address_lower:
            return {
                "district": district_name,
                "latitude": district_data["latitude"],
                "longitude": district_data["longitude"],
                "match_type": "district_name",
                "matched_text": district_name
            }
        
        # Check aliases
        for alias in district_data["aliases"]:
            if alias in address_lower:
                return {
                    "district": district_name,
                    "latitude": district_data["latitude"],
                    "longitude": district_data["longitude"],
                    "match_type": "alias",
                    "matched_text": alias
                }
    
    return None

def generate_customer_id(firebase_uid):
    """Use Firebase UID as customer ID"""
    return firebase_uid

def create_customer_in_db(firebase_uid, name, email, phone, current_location=None):
    """Create customer in MongoDB"""
    try:
        if client is None:
            return None, "Database connection not available"
        
        # Check if customer already exists
        existing_customer = customers_collection.find_one({
            "$or": [
                {"email": email},
                {"firebaseUID": firebase_uid},
                {"customer_id": firebase_uid}
            ]
        })
        
        if existing_customer:
            return None, "Customer already exists with this email or Firebase UID"
        
        # Create customer document
        customer_data = {
            "firebaseUID": firebase_uid,
            "name": name,
            "email": email,
            "role": "customer",
            "customer_id": generate_customer_id(firebase_uid),
            "phone": phone,
            "current_location": current_location or {},
            "order_history": [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        # Insert customer into MongoDB
        result = customers_collection.insert_one(customer_data)
        customer_data['_id'] = str(result.inserted_id)
        
        return customer_data, None
        
    except Exception as e:
        return None, str(e)

@app.route('/customerService', methods=['POST'])
def customer_soap_service():
    try:
        # Parse incoming SOAP request
        root = ET.fromstring(request.data)
        
        # Find the method being called
        for elem in root.iter():
            if elem.tag.endswith('create_customer'):
                # Extract customer data from SOAP request
                firebase_uid = extract_text_by_tag_name(root, 'firebaseUID')
                name = extract_text_by_tag_name(root, 'name')
                email = extract_text_by_tag_name(root, 'email')
                phone = extract_text_by_tag_name(root, 'phone')
                
                # Handle location data with auto-detection
                address = extract_text_by_tag_name(root, 'address')
                provided_latitude = extract_text_by_tag_name(root, 'latitude')
                provided_longitude = extract_text_by_tag_name(root, 'longitude')
                
                current_location = None
                location_info = None
                
                if address:
                    current_location = {"address": address}
                    
                    # Try to auto-detect coordinates from address
                    detected_location = detect_district_from_address(address)
                    
                    if detected_location:
                        # Use detected coordinates
                        current_location["latitude"] = detected_location["latitude"]
                        current_location["longitude"] = detected_location["longitude"]
                        location_info = {
                            "detected_district": detected_location["district"],
                            "match_type": detected_location["match_type"],
                            "matched_text": detected_location["matched_text"],
                            "auto_detected": True
                        }
                        print(f"Auto-detected location: {detected_location['district']} from '{detected_location['matched_text']}'")
                    
                    # Override with provided coordinates if available
                    if provided_latitude and provided_longitude:
                        current_location["latitude"] = float(provided_latitude)
                        current_location["longitude"] = float(provided_longitude)
                        if location_info:
                            location_info["auto_detected"] = False
                            location_info["coordinates_overridden"] = True
                        print("Using provided coordinates instead of auto-detected ones")
                
                elif provided_latitude and provided_longitude:
                    # Only coordinates provided, no address
                    current_location = {
                        "latitude": float(provided_latitude),
                        "longitude": float(provided_longitude)
                    }
                
                # Validate required fields
                if not firebase_uid or not name or not email or not phone:
                    response_body = '<create_customer_response><status>Error</status><message>Missing required fields: firebaseUID, name, email, phone</message></create_customer_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Create customer in database
                customer_data, error = create_customer_in_db(firebase_uid, name, email, phone, current_location)
                
                if error:
                    response_body = f'<create_customer_response><status>Error</status><message>{error}</message></create_customer_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                print(f"Created customer: {customer_data['customer_id']} - {name}")
                
                # Build location info XML if available
                location_info_xml = ""
                if location_info:
                    location_info_xml = f'''<location_info>
                        <detected_district>{location_info['detected_district']}</detected_district>
                        <match_type>{location_info['match_type']}</match_type>
                        <matched_text>{location_info['matched_text']}</matched_text>
                        <auto_detected>{str(location_info['auto_detected']).lower()}</auto_detected>
                        {'<coordinates_overridden>true</coordinates_overridden>' if location_info.get('coordinates_overridden') else ''}
                    </location_info>'''
                
                response_body = f'''<create_customer_response>
                    <status>Success</status>
                    <customer_id>{customer_data['customer_id']}</customer_id>
                    <message>Customer created successfully</message>
                    {location_info_xml}
                </create_customer_response>'''
                return Response(create_soap_response(response_body), content_type='text/xml')
                
            elif elem.tag.endswith('get_customer'):
                customer_id = extract_text_by_tag_name(root, 'customer_id')
                
                if not customer_id:
                    response_body = '<get_customer_response><status>Error</status><message>Customer ID is required</message></get_customer_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                if client is None:
                    response_body = '<get_customer_response><status>Error</status><message>Database connection not available</message></get_customer_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                customer = customers_collection.find_one({"customer_id": customer_id})
                
                if not customer:
                    response_body = '<get_customer_response><status>Error</status><message>Customer not found</message></get_customer_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                print(f"Retrieved customer: {customer_id}")
                
                location_xml = ""
                if customer.get('current_location'):
                    loc = customer['current_location']
                    location_xml = f'''<current_location>
                        <address>{loc.get('address', '')}</address>
                        <latitude>{loc.get('latitude', '')}</latitude>
                        <longitude>{loc.get('longitude', '')}</longitude>
                    </current_location>'''
                
                response_body = f'''<get_customer_response>
                    <status>Success</status>
                    <customer>
                        <customer_id>{customer['customer_id']}</customer_id>
                        <firebaseUID>{customer['firebaseUID']}</firebaseUID>
                        <name>{customer['name']}</name>
                        <email>{customer['email']}</email>
                        <phone>{customer['phone']}</phone>
                        <role>{customer['role']}</role>
                        {location_xml}
                    </customer>
                </get_customer_response>'''
                return Response(create_soap_response(response_body), content_type='text/xml')
        
        return Response("Method not found", status=400)
        
    except Exception as e:
        print(f"Error in customer SOAP service: {str(e)}")
        response_body = f'<soap_error><status>Error</status><message>Internal server error: {str(e)}</message></soap_error>'
        return Response(create_soap_response(response_body), content_type='text/xml', status=500)

@app.route('/orderService', methods=['POST'])
def order_soap_service():
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
                              
            elif elem.tag.endswith('create_order'):
                # Extract order data from SOAP request
                order_id = extract_text_by_tag_name(root, 'orderID')
                customer_id = extract_text_by_tag_name(root, 'customer_id')
                total_amount = extract_text_by_tag_name(root, 'totalAmount')
                priority = extract_text_by_tag_name(root, 'priority') or 'medium'
                
                # Validate required fields
                if not order_id or not customer_id or not total_amount:
                    response_body = '<create_order_response><status>Error</status><message>Missing required fields: orderID, customer_id, totalAmount</message></create_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                if client is None:
                    response_body = '<create_order_response><status>Error</status><message>Database connection not available</message></create_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Verify customer exists
                customer = customers_collection.find_one({"customer_id": customer_id})
                if not customer:
                    response_body = '<create_order_response><status>Error</status><message>Customer not found</message></create_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Extract items array
                items = []
                for item_elem in root.iter():
                    if item_elem.tag.endswith('item'):
                        product_id = None
                        name = None
                        quantity = None
                        price = None
                        
                        for child in item_elem:
                            if child.tag.endswith('product_id'):
                                product_id = child.text
                            elif child.tag.endswith('name'):
                                name = child.text
                            elif child.tag.endswith('quantity'):
                                quantity = int(child.text) if child.text else 1
                            elif child.tag.endswith('price'):
                                price = float(child.text) if child.text else 0.0
                        
                        if product_id and name:
                            items.append({
                                "product_id": product_id,
                                "name": name,
                                "quantity": quantity or 1,
                                "price": price or 0.0
                            })
                
                # Create order document
                order_data = {
                    "orderID": order_id,
                    "customer_id": customer_id,
                    "items": items,
                    "totalAmount": float(total_amount),
                    "priority": priority,
                    "status": "pending",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
                
                # Insert order into orders collection
                try:
                    result = orders_collection.insert_one(order_data)
                    order_data['_id'] = str(result.inserted_id)
                    
                    print(f"Created order {order_id} for customer {customer_id}")
                    
                    response_body = f'''<create_order_response>
                        <status>Success</status>
                        <message>Order created successfully</message>
                        <orderID>{order_id}</orderID>
                        <customer_id>{customer_id}</customer_id>
                        <totalAmount>{total_amount}</totalAmount>
                        <order_status>pending</order_status>
                    </create_order_response>'''
                    return Response(create_soap_response(response_body), content_type='text/xml')
                    
                except Exception as e:
                    response_body = f'<create_order_response><status>Error</status><message>Database error: {str(e)}</message></create_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                    
            elif elem.tag.endswith('get_customer_orders'):
                customer_id = extract_text_by_tag_name(root, 'customer_id')
                
                if not customer_id:
                    response_body = '<get_customer_orders_response><status>Error</status><message>Customer ID is required</message></get_customer_orders_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                if client is None:
                    response_body = '<get_customer_orders_response><status>Error</status><message>Database connection not available</message></get_customer_orders_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Verify customer exists
                customer = customers_collection.find_one({"customer_id": customer_id})
                if not customer:
                    response_body = '<get_customer_orders_response><status>Error</status><message>Customer not found</message></get_customer_orders_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Get all orders for this customer
                orders = list(orders_collection.find({"customer_id": customer_id}))
                
                print(f"Retrieved {len(orders)} orders for customer: {customer_id}")
                
                # Build orders XML
                orders_xml = ""
                for order in orders:
                    # Build items XML for this order
                    items_xml = ""
                    if order.get('items'):
                        for item in order['items']:
                            items_xml += f'''<item>
                                <product_id>{item.get('product_id', '')}</product_id>
                                <name>{item.get('name', '')}</name>
                                <quantity>{item.get('quantity', 0)}</quantity>
                                <price>{item.get('price', 0)}</price>
                            </item>'''
                    
                    created_at = order.get('created_at', '')
                    if isinstance(created_at, datetime):
                        created_at = created_at.isoformat()
                    
                    orders_xml += f'''<order>
                        <orderID>{order.get('orderID', '')}</orderID>
                        <totalAmount>{order.get('totalAmount', 0)}</totalAmount>
                        <priority>{order.get('priority', '')}</priority>
                        <status>{order.get('status', 'pending')}</status>
                        <created_at>{created_at}</created_at>
                        <items>{items_xml}</items>
                    </order>'''
                
                response_body = f'''<get_customer_orders_response>
                    <status>Success</status>
                    <customer_id>{customer_id}</customer_id>
                    <orders_count>{len(orders)}</orders_count>
                    <orders>
                        {orders_xml}
                    </orders>
                </get_customer_orders_response>'''
                return Response(create_soap_response(response_body), content_type='text/xml')
                
            elif elem.tag.endswith('get_order'):
                order_id = extract_text_by_tag_name(root, 'orderID')
                
                if not order_id:
                    response_body = '<get_order_response><status>Error</status><message>Order ID is required</message></get_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                if client is None:
                    response_body = '<get_order_response><status>Error</status><message>Database connection not available</message></get_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                # Find the order
                order = orders_collection.find_one({"orderID": order_id})
                
                if not order:
                    response_body = '<get_order_response><status>Error</status><message>Order not found</message></get_order_response>'
                    return Response(create_soap_response(response_body), content_type='text/xml')
                
                print(f"Retrieved order: {order_id}")
                
                # Build items XML
                items_xml = ""
                if order.get('items'):
                    for item in order['items']:
                        items_xml += f'''<item>
                            <product_id>{item.get('product_id', '')}</product_id>
                            <name>{item.get('name', '')}</name>
                            <quantity>{item.get('quantity', 0)}</quantity>
                            <price>{item.get('price', 0)}</price>
                        </item>'''
                
                created_at = order.get('created_at', '')
                if isinstance(created_at, datetime):
                    created_at = created_at.isoformat()
                
                response_body = f'''<get_order_response>
                    <status>Success</status>
                    <order>
                        <orderID>{order.get('orderID', '')}</orderID>
                        <customer_id>{order.get('customer_id', '')}</customer_id>
                        <totalAmount>{order.get('totalAmount', 0)}</totalAmount>
                        <priority>{order.get('priority', '')}</priority>
                        <status>{order.get('status', 'pending')}</status>
                        <created_at>{created_at}</created_at>
                        <items>{items_xml}</items>
                    </order>
                </get_order_response>'''
                return Response(create_soap_response(response_body), content_type='text/xml')
        
        return Response("Method not found", status=400)
        
    except Exception as e:
        return Response(f"Error: {str(e)}", status=500)

@app.route('/getOrders/<customerID>', methods=['GET'])
def get_all_orders_by_customer(customerID):
    """SOAP/XML endpoint to get all orders for a specific customer using path parameter"""
    try:
        if not customerID:
            response_body = '<get_orders_response><status>Error</status><message>Customer ID is required</message></get_orders_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        if client is None:
            response_body = '<get_orders_response><status>Error</status><message>Database connection not available</message></get_orders_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        # Verify customer exists
        customer = customers_collection.find_one({"customer_id": customerID})
        if not customer:
            response_body = '<get_orders_response><status>Error</status><message>Customer not found</message></get_orders_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        # Get all orders for this customer
        orders = list(orders_collection.find({"customer_id": customerID}))
        
        print(f"Retrieved {len(orders)} orders for customer: {customerID}")
        
        # Build orders XML
        orders_xml = ""
        for order in orders:
            # Build items XML for this order
            items_xml = ""
            if order.get('items'):
                for item in order['items']:
                    items_xml += f'''<item>
                        <product_id>{item.get('product_id', '')}</product_id>
                        <name>{item.get('name', '')}</name>
                        <quantity>{item.get('quantity', 0)}</quantity>
                        <price>{item.get('price', 0)}</price>
                    </item>'''
            
            created_at = order.get('created_at', '')
            updated_at = order.get('updated_at', '')
            if isinstance(created_at, datetime):
                created_at = created_at.isoformat()
            if isinstance(updated_at, datetime):
                updated_at = updated_at.isoformat()
            
            orders_xml += f'''<order>
                <_id>{str(order.get('_id', ''))}</_id>
                <orderID>{order.get('orderID', '')}</orderID>
                <customer_id>{order.get('customer_id', '')}</customer_id>
                <totalAmount>{order.get('totalAmount', 0)}</totalAmount>
                <priority>{order.get('priority', '')}</priority>
                <status>{order.get('status', 'pending')}</status>
                <created_at>{created_at}</created_at>
                <updated_at>{updated_at}</updated_at>
                <items>{items_xml}</items>
            </order>'''
        
        response_body = f'''<get_orders_response>
            <status>Success</status>
            <customer_id>{customerID}</customer_id>
            <orders_count>{len(orders)}</orders_count>
            <orders>
                {orders_xml}
            </orders>
        </get_orders_response>'''
        
        return Response(create_soap_response(response_body), content_type='text/xml')
        
    except Exception as e:
        print(f"Error in get all orders endpoint: {str(e)}")
        response_body = f'<get_orders_response><status>Error</status><message>Internal server error: {str(e)}</message></get_orders_response>'
        return Response(create_soap_response(response_body), content_type='text/xml', status=500)

@app.route('/orderService', methods=['GET'])
def order_wsdl():
    if request.args.get('wsdl') is not None:
        # Return a simple WSDL (you'd want a more complete one in production)
        wsdl_content = """<?xml version="1.0" encoding="utf-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/">
    <!-- WSDL content would go here -->
    <message name="Simple WSDL - implement full WSDL as needed"/>
</definitions>"""
        return Response(wsdl_content, content_type='text/xml')
    return "SOAP Service"

@app.route('/api/updateStatus', methods=['POST'])
def update_order_status():
    """SOAP/XML endpoint to update order status using only orderID and status tags.
    Accepts raw XML (optionally wrapped in SOAP envelope)."""
    try:
        if client is None:
            response_body = '<update_status_response><status>Error</status><message>Database connection not available</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')

        try:
            root = ET.fromstring(request.data)
        except Exception as e:
            response_body = f'<update_status_response><status>Error</status><message>Invalid XML: {str(e)}</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        # Helper to match by local-name ignoring namespaces
        def first_by_local_name(root_elem, local_name):
            for el in root_elem.iter():
                if el.tag.split('}')[-1] == local_name:
                    return el
            return None

        # Narrow scope to <update_order_status> if present to avoid picking up other <status> nodes
        update_node = first_by_local_name(root, 'update_order_status') or root

        order_id = None
        status_value = None

        # Prefer direct children of update_order_status
        for child in list(update_node):
            local = child.tag.split('}')[-1]
            if local == 'orderID':
                order_id = (child.text or '').strip()
            elif local == 'status':
                status_value = (child.text or '').strip()

        # Fallback if not found
        if not order_id:
            order_id = (extract_text_by_tag_name(root, 'orderID') or '').strip()
        if not status_value:
            status_value = (extract_text_by_tag_name(root, 'status') or '').strip()

        if not order_id or not status_value:
            response_body = '<update_status_response><status>Error</status><message>Missing required fields: orderID and status</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')

        # Validate non-empty after stripping whitespace
        if status_value == '':
            response_body = '<update_status_response><status>Error</status><message>Status value is empty</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')

        order = orders_collection.find_one({"orderID": order_id})
        if not order:
            response_body = '<update_status_response><status>Error</status><message>Order not found</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')

        try:
            orders_collection.update_one({"orderID": order_id}, {"$set": {"status": status_value, "updated_at": datetime.utcnow()}})
            print(f"[updateStatus] Updated order {order_id} to status '{status_value}'")
        except Exception as e:
            response_body = f'<update_status_response><status>Error</status><message>Database error: {str(e)}</message></update_status_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')

        response_body = f'''<update_status_response>\n            <status>Success</status>\n            <orderID>{order_id}</orderID>\n            <new_status>{status_value}</new_status>\n            <message>Order status updated successfully</message>\n        </update_status_response>'''
        return Response(create_soap_response(response_body), content_type='text/xml')
    except Exception as e:
        print(f"Error in update_status endpoint: {str(e)}")
        response_body = f'<update_status_response><status>Error</status><message>Internal server error: {str(e)}</message></update_status_response>'
        return Response(create_soap_response(response_body), content_type='text/xml', status=500)

@app.route('/getDeliveryLocation', methods=['POST'])
def get_delivery_location():
    """SOAP/XML endpoint to get delivery location for an order.
    Finds customer_id from orders table, then retrieves current_location from customers table."""
    try:
        if client is None:
            response_body = '<get_delivery_location_response><status>Error</status><message>Database connection not available</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        # Parse incoming SOAP request to extract orderID
        try:
            root = ET.fromstring(request.data)
        except Exception as e:
            response_body = f'<get_delivery_location_response><status>Error</status><message>Invalid XML: {str(e)}</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        orderID = extract_text_by_tag_name(root, 'orderID')
        
        if not orderID:
            response_body = '<get_delivery_location_response><status>Error</status><message>Order ID is required in payload</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        # Find the order to get customer_id
        order = orders_collection.find_one({"orderID": orderID})
        if not order:
            response_body = '<get_delivery_location_response><status>Error</status><message>Order not found</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        customer_id = order.get('customer_id')
        if not customer_id:
            response_body = '<get_delivery_location_response><status>Error</status><message>Customer ID not found in order</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        # Find the customer to get current_location
        customer = customers_collection.find_one({"customer_id": customer_id})
        if not customer:
            response_body = '<get_delivery_location_response><status>Error</status><message>Customer not found</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        current_location = customer.get('current_location', {})
        if not current_location:
            response_body = '<get_delivery_location_response><status>Error</status><message>No delivery location found for customer</message></get_delivery_location_response>'
            return Response(create_soap_response(response_body), content_type='text/xml')
        
        print(f"[getDeliveryLocation] Retrieved delivery location for order {orderID}, customer {customer_id}")
        
        # Extract additional customer and order information
        customer_name = customer.get('name', '')
        customer_phone = customer.get('phone', '')
        order_priority = order.get('priority', '')
        order_status = order.get('status', '')
        
        # Build location XML
        address = current_location.get('address', '')
        latitude = current_location.get('latitude', '')
        longitude = current_location.get('longitude', '')
        
        response_body = f'''<get_delivery_location_response>
            <status>Success</status>
            <orderID>{orderID}</orderID>
            <customer_id>{customer_id}</customer_id>
            <customer_name>{customer_name}</customer_name>
            <customer_phone>{customer_phone}</customer_phone>
            <priority>{order_priority}</priority>
            <order_status>{order_status}</order_status>
            <delivery_location>
                <address>{address}</address>
                <latitude>{latitude}</latitude>
                <longitude>{longitude}</longitude>
            </delivery_location>
            <message>Delivery location retrieved successfully</message>
        </get_delivery_location_response>'''
        
        return Response(create_soap_response(response_body), content_type='text/xml')
        
    except Exception as e:
        print(f"Error in get_delivery_location endpoint: {str(e)}")
        response_body = f'<get_delivery_location_response><status>Error</status><message>Internal server error: {str(e)}</message></get_delivery_location_response>'
        return Response(create_soap_response(response_body), content_type='text/xml', status=500)

if __name__ == '__main__':
    print("CMS SOAP Server listening on http://127.0.0.1:8000")
    print("Available SOAP endpoints:")
    print("  - Customer Service: POST http://127.0.0.1:8000/customerService")
    print("    * create_customer (firebaseUID, name, email, phone, [address, latitude, longitude])")
    print("    * get_customer (customer_id)")
    print("  - Customer WSDL: GET http://127.0.0.1:8000/customerService?wsdl")
    print("  - Order Service: POST http://127.0.0.1:8000/orderService")
    print("    * new_package, update_package, get_package_status")
    print("    * create_order (orderID, customer_id, totalAmount, priority, items[])")
    print("    * get_customer_orders (customer_id)")
    print("    * get_order (orderID)")
    print("  - Order WSDL: GET http://127.0.0.1:8000/orderService?wsdl")
    print("  - Get All Orders: GET http://127.0.0.1:8000/getOrders/<customerID>")
    print("    * Returns all orders for a specific customer in SOAP/XML format")
    print("  - Update Order Status: POST http://127.0.0.1:8000/api/updateStatus (orderID, status)")
    print("  - Get Delivery Location: POST http://127.0.0.1:8000/getDeliveryLocation (orderID in payload)")
    print("    * Returns delivery location for an order by finding customer's current_location")
    
    app.run(host='127.0.0.1', port=8000, debug=True)