from flask import Flask, render_template, request, redirect, url_for, jsonify
import json, os
import datetime

app = Flask(__name__)

#----------------------------------------------------------------------------------------------------------------------
#DATA LOADING
#----------------------------------------------------------------------------------------------------------------------

DATA_DIR = os.path.join(app.root_path, "data")

def load_json(filename):
    with open(os.path.join(DATA_DIR, filename), "r") as f:
        return json.load(f)

customers     = load_json("customers.json")
orders        = load_json("orders.json")
messages      = load_json("messages.json")
complaints    = load_json("complaints.json")
subscriptions = load_json("subscriptions.json")
activity      = load_json("activity.json")

#----------------------------------------------------------------------------------------------------------------------
#NORMALISERS AND INDEXERS
#----------------------------------------------------------------------------------------------------------------------

def GR(s):  # normalize GR keys consistently
    return (s or "").strip().upper()

def norm(s):  # for general text matching
    return (s or "").strip().lower().replace(" ", "")

customers_by_gr     = {GR(c["gr"]): c for c in customers}
messages_by_gr      = {GR(m["gr"]): m for m in messages}
subscriptions_by_gr = {GR(s["gr"]): s for s in subscriptions}

#----------------------------------------------------------------------------------------------------------------------
#DYNAMIC PAYMENTS GENERATION
#----------------------------------------------------------------------------------------------------------------------

def generate_payments_for_gr(gr):
    """Generate payment data for a specific GR from orders"""
    g = GR(gr)
    customer_orders = [o for o in orders if GR(o.get("gr")) == g]
    
    if not customer_orders:
        return None
    
    # Create payment transactions from orders
    payment_log = []
    for order in customer_orders:
        payment_log.append({
            "date": order.get("order_date", ""),
            "order_id": order.get("order_id", ""),
            "amount": order.get("payment", 0),
            "status": "paid" if order.get("status") == "committed" else "pending",
            "txn_id": f"txn_{order.get('order_id', '').lower().replace('-', '_')}"
        })
    
    # Sort by date, most recent first
    payment_log.sort(key=lambda x: x["date"], reverse=True)
    
    # Determine payment method
    gr_num = gr[-3:] if len(gr) >= 3 else "001"
    method_map = {
        "001": "Visa **** 1234",
        "002": "Mastercard **** 5678", 
        "003": "Amex **** 9012",
        "004": "Visa **** 4455",
        "005": "Visa **** 7788",
        "006": "Apple Pay (Amex **** 3456)",
        "007": "Mastercard **** 1122"
    }
    method = method_map.get(gr_num, "Visa **** 0000")
    
    return {
        "gr": gr,
        "method": method,
        "log": payment_log
    }

def get_all_payments():
    """Generate all payments data from orders, grouped by GR"""
    payments_by_gr = {}
    
    # Get all unique GRs from orders
    unique_grs = set(GR(o.get("gr")) for o in orders if o.get("gr"))
    
    for gr in unique_grs:
        payment_data = generate_payments_for_gr(gr)
        if payment_data:
            payments_by_gr[gr] = payment_data
    
    return payments_by_gr

#----------------------------------------------------------------------------------------------------------------------
#HELPERS DATA FETCHING 
#----------------------------------------------------------------------------------------------------------------------
def orders_for(gr):
    g = GR(gr)
    return [o for o in orders if GR(o.get("gr")) == g]

def complaints_for(gr):
    g = GR(gr)
    return [c for c in complaints if GR(c.get("gr")) == g]

def activity_for(gr):
    g = GR(gr)
    return sorted(
        [e for e in activity if GR(e.get("gr")) == g],
        key=lambda e: e["time"],
        reverse=True
    )

def get_customer_info(gr):
    g = GR(gr)
    cust = customers_by_gr.get(g)
    if not cust:
        return None
    sub = subscriptions_by_gr.get(g)
    return {
        "name": f"{cust['first_name']} {cust['last_name']}",
        "gr": cust["gr"], 
        "postcode": cust["postcode"],
        "subscription_status": (sub["status"] if sub else "Unknown"),
    }

def ctx_for(gr, **extra):
    g = GR(gr) if gr else None
    return {
        "selected_gr": g,
        "customer_info": get_customer_info(g) if g else None,
        **extra
    }

#----------------------------------------------------------------------------------------------------------------------
#CORE ROUTES
#----------------------------------------------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def dashboard():
    by = (request.args.get("by") or "").strip()
    q  = (request.args.get("q")  or "").strip()
    results = []

    if by and q:
        # GR: exact match after normalization; others: partial
        if by == "gr":
            qg = GR(q)
            results = [c for c in customers if GR(c.get("gr")) == qg]
        else:
            qn = norm(q)
            for c in customers:
                if by == "full_name" and qn in norm(f"{c.get('first_name','')} {c.get('last_name','')}"):
                    results.append(c)
                elif by == "email"    and qn in norm(c.get("email","")):
                    results.append(c)
                elif by == "phone"    and qn in norm(c.get("phone","")):
                    results.append(c)
                elif by == "postcode" and qn in norm(c.get("postcode","")):
                    results.append(c)

    return render_template(
        "dashboard.html",
        page="dashboard",
        search={"by": by, "q": q},
        results=results,
        selected_gr=request.args.get("gr"),
        customer_info=get_customer_info(request.args.get("gr")) if request.args.get("gr") else None
    )

#----------------------------------------------------------------------------------------------------------------------
#PERSONAL ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/personal")
def personal():
    gr = request.args.get("gr")
    cust = customers_by_gr.get(GR(gr)) if gr else None
    return render_template(
        "personal.html",
        page="personal",
        **ctx_for(gr, customer=cust)
    )

#----------------------------------------------------------------------------------------------------------------------
#UPDATE PERSONAL DETAILS ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/update_personal", methods=["POST"])
def update_personal():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        gr = data.get('gr')
        if not gr:
            return jsonify({"success": False, "error": "GR is required"}), 400
        
        # Validate required fields
        required_fields = ['first_name', 'last_name', 'email', 'phone', 'postcode']
        for field in required_fields:
            if not data.get(field) or not data.get(field).strip():
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        # SANITIZATION AND VALIDATION
        
        # 1. Name sanitization - capitalize first letter
        first_name = data.get('first_name').strip().capitalize()
        last_name = data.get('last_name').strip().capitalize()
        
        # 2. Email validation - must contain @
        email = data.get('email').strip().lower()
        if '@' not in email:
            return jsonify({"success": False, "error": "Email address must contain an @ symbol"}), 400
        
        # Advanced email format validation
        import re
        email_pattern = r'^[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        if not re.match(email_pattern, email):
            return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
        
        # 3. Phone validation - must be exactly 11 digits
        phone = re.sub(r'[^\d]', '', data.get('phone'))  # Remove all non-digit characters
        if len(phone) != 11:
            return jsonify({"success": False, "error": "Phone number must be exactly 11 digits"}), 400
        
        # Format phone number nicely (07123 456789)
        phone = f"{phone[:5]} {phone[5:]}"
        
        # 4. Postcode sanitization - uppercase and proper spacing
        postcode = data.get('postcode').strip().upper().replace(' ', '')
        
        # Validate UK postcode format and add proper spacing
        if len(postcode) == 5:  # Format: XX XXX
            postcode = f"{postcode[:2]} {postcode[2:]}"
        elif len(postcode) == 6:  # Format: XXX XXX
            postcode = f"{postcode[:3]} {postcode[3:]}"
        elif len(postcode) == 7:  # Format: XXXX XXX
            postcode = f"{postcode[:4]} {postcode[4:]}"
        else:
            return jsonify({"success": False, "error": "Invalid postcode format. Please use formats like SW1A 1AA, B1 1BB, or M1 2AB"}), 400
        
        # 5. GR sanitization - uppercase, no spaces
        gr = gr.strip().upper().replace(' ', '')
        
        # 6. Address sanitization - capitalize first letter of each word
        address = data.get('address', '').strip()
        if address:
            address = ' '.join(word.capitalize() for word in address.split())
        
        # 7. City sanitization - capitalize first letter of each word
        city = data.get('city', '').strip()
        if city:
            city = ' '.join(word.capitalize() for word in city.split())
        
        # Find the customer
        customer_found = False
        old_customer = None
        
        for i, customer in enumerate(customers):
            if GR(customer.get('gr')) == GR(gr):
                old_customer = customer.copy()
                
                # Update with sanitized data
                customers[i]['first_name'] = first_name
                customers[i]['last_name'] = last_name
                customers[i]['email'] = email
                customers[i]['phone'] = phone
                customers[i]['postcode'] = postcode
                customers[i]['address'] = address
                customers[i]['city'] = city
                
                customer_found = True
                updated_customer = customers[i]
                break
        
        if not customer_found:
            return jsonify({"success": False, "error": "Customer not found"}), 404
        
        # Save to file
        customers_file_path = os.path.join(DATA_DIR, "customers.json")
        with open(customers_file_path, "w") as f:
            json.dump(customers, f, indent=2)
        
        # Update the customers_by_gr index
        customers_by_gr[GR(gr)] = updated_customer
        
        # Log activity
        changes = []
        if old_customer.get('first_name', '') != updated_customer['first_name']:
            changes.append(f"First name: {old_customer.get('first_name', '')} → {updated_customer['first_name']}")
        if old_customer.get('last_name', '') != updated_customer['last_name']:
            changes.append(f"Last name: {old_customer.get('last_name', '')} → {updated_customer['last_name']}")
        if old_customer.get('email', '') != updated_customer['email']:
            changes.append(f"Email: {old_customer.get('email', '')} → {updated_customer['email']}")
        if old_customer.get('phone', '') != updated_customer['phone']:
            changes.append(f"Phone: {old_customer.get('phone', '')} → {updated_customer['phone']}")
        if old_customer.get('postcode', '') != updated_customer['postcode']:
            changes.append(f"Postcode: {old_customer.get('postcode', '')} → {updated_customer['postcode']}")
        if old_customer.get('address', '') != updated_customer['address']:
            changes.append(f"Address: {old_customer.get('address', '')} → {updated_customer['address']}")
        if old_customer.get('city', '') != updated_customer['city']:
            changes.append(f"City: {old_customer.get('city', '')} → {updated_customer['city']}")
        
        if changes:
            activity_entry = {
                "gr": gr,
                "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "category": "personal_updated",
                "actor": "admin",
                "description": "Personal details updated",
                "detail": f"{len(changes)} field{'s' if len(changes) > 1 else ''} modified",
                "changes": changes
            }
            activity.append(activity_entry)
            
            # Save activity log
            activity_file_path = os.path.join(DATA_DIR, "activity.json")
            with open(activity_file_path, "w") as f:
                json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Personal details updated successfully",
            "customer": updated_customer
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500
    
#----------------------------------------------------------------------------------------------------------------------
#SUBSCRIPTION ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/subscription")
def subscription():
    gr = request.args.get("gr")
    if gr:
        subscription_data = subscriptions_by_gr.get(GR(gr))
    else:
        subscription_data = None
    
    return render_template(
        "subscription.html", 
        page="subscription", 
        **ctx_for(gr, subscription=subscription_data)
    )

#----------------------------------------------------------------------------------------------------------------------
#SUBSCRIPTION UPDATE ROUTE - WITH SMART ACTIVITY LOGGING
#----------------------------------------------------------------------------------------------------------------------

@app.route("/update_subscription", methods=["POST"])
def update_subscription():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        gr = data.get("gr")
        if not gr:
            return jsonify({"success": False, "error": "No GR provided"}), 400
        
        normalized_gr = GR(gr)
        
        subscription_found = False
        old_subscription = None
        
        for i, sub in enumerate(subscriptions):
            if GR(sub.get("gr")) == normalized_gr:
                old_subscription = sub.copy()
                
                subscriptions[i]["status"] = data.get("status", sub["status"])
                subscriptions[i]["frequency"] = data.get("frequency", sub["frequency"])
                subscriptions[i]["recipes"] = int(data.get("recipes", sub["recipes"]))
                subscriptions[i]["box_size"] = int(data.get("box_size", sub["box_size"]))
                subscriptions[i]["delivery_day"] = data.get("delivery_day", sub["delivery_day"])
                
                subscription_found = True
                new_subscription = subscriptions[i]
                break
        
        if not subscription_found:
            return jsonify({"success": False, "error": "Subscription not found"}), 404
        
        subscriptions_file_path = os.path.join(DATA_DIR, "subscriptions.json")
        with open(subscriptions_file_path, "w") as f:
            json.dump(subscriptions, f, indent=2)
        
        subscriptions_by_gr[normalized_gr] = new_subscription
        
        changes = []
        change_details = {}
        
        if old_subscription["status"] != new_subscription["status"]:
            changes.append(f"Status: {old_subscription['status']} → {new_subscription['status']}")
            change_details["status"] = {"old": old_subscription["status"], "new": new_subscription["status"]}
            
        if old_subscription["frequency"] != new_subscription["frequency"]:
            changes.append(f"Frequency: {old_subscription['frequency']} → {new_subscription['frequency']}")
            change_details["frequency"] = {"old": old_subscription["frequency"], "new": new_subscription["frequency"]}
            
        if old_subscription["recipes"] != new_subscription["recipes"]:
            changes.append(f"Recipes: {old_subscription['recipes']} → {new_subscription['recipes']}")
            change_details["recipes"] = {"old": old_subscription["recipes"], "new": new_subscription["recipes"]}
            
        if old_subscription["box_size"] != new_subscription["box_size"]:
            changes.append(f"Box size: {old_subscription['box_size']} → {new_subscription['box_size']}")
            change_details["box_size"] = {"old": old_subscription["box_size"], "new": new_subscription["box_size"]}
            
        if old_subscription["delivery_day"] != new_subscription["delivery_day"]:
            changes.append(f"Delivery day: {old_subscription['delivery_day']} → {new_subscription['delivery_day']}")
            change_details["delivery_day"] = {"old": old_subscription["delivery_day"], "new": new_subscription["delivery_day"]}
        
        if changes:
            activity_entry = {
                "gr": gr,
                "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "category": "subscription_update",
                "actor": "admin",
                "description": "Subscription updated", 
                "detail": f"{len(changes)} field{'s' if len(changes) > 1 else ''} modified",  
                "changes": changes,  
                "details": change_details  
            }
            activity.append(activity_entry)
            
            activity_file_path = os.path.join(DATA_DIR, "activity.json")
            with open(activity_file_path, "w") as f:
                json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True, 
            "message": "Subscription updated successfully",
            "updated_subscription": new_subscription,
            "changes": changes
        })
        
    except ValueError as e:
        return jsonify({"success": False, "error": f"Invalid data format: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

#----------------------------------------------------------------------------------------------------------------------
#ORDERS ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/orders")
def orders_page():
    gr = request.args.get("gr")
    if gr:
        order_list = orders_for(gr)
    else:
        order_list = []
    
    return render_template("orders.html", page="orders", **ctx_for(gr, orders=order_list))

#----------------------------------------------------------------------------------------------------------------------
#GENERATE ORDER ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/generate_order", methods=["POST"])
def generate_order():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        gr = data.get("gr")
        if not gr:
            return jsonify({"success": False, "error": "No GR provided"}), 400
        
        delivery_date = data.get("delivery_date")
        box_size = data.get("box_size")
        recipes = data.get("recipes", [])
        
        # Validate delivery date (must be at least 3 days from now)
        from datetime import datetime, timedelta
        try:
            delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
            min_date = datetime.now() + timedelta(days=3)
            
            if delivery_dt < min_date:
                return jsonify({"success": False, "error": "Delivery date must be at least 3 days from today"}), 400
        except ValueError:
            return jsonify({"success": False, "error": "Invalid delivery date format"}), 400
        
        # Validate other required fields
        if not box_size or not isinstance(box_size, int) or box_size < 1 or box_size > 5:
            return jsonify({"success": False, "error": "Invalid box size"}), 400
        
        if not recipes or len(recipes) < 2 or len(recipes) > 5:
            return jsonify({"success": False, "error": "Must select between 2-5 recipes"}), 400
        
        # Calculate pricing
        recipe_prices = {
            'honey-garlic-chicken': 6.99,
            'beef-tacos': 7.49,
            'salmon-teriyaki': 8.99,
            'vegetarian-curry': 5.99,
            'pasta-carbonara': 6.49,
            'thai-green-curry': 7.99,
            'mushroom-risotto': 6.99
        }
        
        total_payment = 0
        for recipe in recipes:
            recipe_id = recipe.get('id', '')
            if recipe_id in recipe_prices:
                total_payment += recipe_prices[recipe_id] * box_size
        
        # Generate unique order ID
        import uuid
        order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create new order
        new_order = {
            "gr": gr,
            "order_id": order_id,
            "order_date": f"{delivery_date}T12:00:00Z",  # Convert to datetime format to match existing orders
            "status": "pending",
            "box_size": box_size,
            "recipes": recipes,
            "payment": round(total_payment, 2),
            "courier_details": None  # Will be populated closer to delivery
        }
        
        # Add to orders list
        orders.append(new_order)
        
        # Save to file
        orders_file_path = os.path.join(DATA_DIR, "orders.json")
        with open(orders_file_path, "w") as f:
            json.dump(orders, f, indent=2)
        
        # Log activity
        activity_entry = {
            "gr": gr,
            "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "category": "order_created",
            "actor": "admin",
            "description": "Order generated",
            "detail": f"Order {order_id} created for {delivery_date}"
        }
        activity.append(activity_entry)
        
        # Save activity log
        activity_file_path = os.path.join(DATA_DIR, "activity.json")
        with open(activity_file_path, "w") as f:
            json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Order generated successfully",
            "order": new_order
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500
    
#----------------------------------------------------------------------------------------------------------------------
#CANCEL ORDER ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/cancel_order", methods=["POST"])
def cancel_order():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        order_id = data.get("order_id")
        if not order_id:
            return jsonify({"success": False, "error": "Order ID is required"}), 400
        
        # Find and remove the order
        order_found = False
        order_gr = None
        removed_order = None
        
        for i, order in enumerate(orders):
            if order.get("order_id") == order_id:
                # Check if order can be cancelled (only pending orders)
                if order.get("status") != "pending":
                    return jsonify({"success": False, "error": "Only pending orders can be cancelled"}), 400
                
                # Store order info for logging before removal
                order_gr = order.get("gr")
                removed_order = order.copy()
                
                # Remove the order from the list completely
                orders.pop(i)
                order_found = True
                break
        
        if not order_found:
            return jsonify({"success": False, "error": "Order not found"}), 404
        
        # Save updated orders to file (order is now completely removed)
        orders_file_path = os.path.join(DATA_DIR, "orders.json")
        with open(orders_file_path, "w") as f:
            json.dump(orders, f, indent=2)
        
        # Log activity
        if order_gr and removed_order:
            activity_entry = {
                "gr": order_gr,
                "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "category": "order_deleted",
                "actor": "admin",
                "description": "Order cancelled and removed",
                "detail": f"Order {order_id} was cancelled and permanently removed (£{removed_order.get('payment', 0):.2f})"
            }
            activity.append(activity_entry)
            
            # Save activity log
            activity_file_path = os.path.join(DATA_DIR, "activity.json")
            with open(activity_file_path, "w") as f:
                json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Order cancelled and removed successfully"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500
    
#----------------------------------------------------------------------------------------------------------------------
#PAYMENTS ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/payments")
def payments_page():
    gr = request.args.get("gr")
    if gr:
        payment_data = generate_payments_for_gr(gr)
    else:
        payment_data = None
    
    return render_template("payments.html", page="payments",
                           **ctx_for(gr, payments=payment_data))

#----------------------------------------------------------------------------------------------------------------------
#ACTIVITY ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/activity")
def activity_page():
    gr = request.args.get("gr")
    type_filter  = request.args.get("type") or ""
    actor_filter = request.args.get("actor") or ""

    events = activity_for(gr) if gr else []
    
    # Apply filters
    if type_filter:
        events = [e for e in events if e.get("category") == type_filter]
    if actor_filter:
        events = [e for e in events if e.get("actor") == actor_filter]

    return render_template("activity.html", page="activity",
                           **ctx_for(gr, activity=events, filters={"type": type_filter, "actor": actor_filter}))

#----------------------------------------------------------------------------------------------------------------------
#CLEAR ACTIVITY LOG ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/clear_activity_log", methods=["POST"])
def clear_activity_log():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        gr = data.get("gr")
        if not gr:
            return jsonify({"success": False, "error": "No GR provided"}), 400
        
        normalized_gr = GR(gr)
        
        global activity
        original_count = len(activity)
        activity = [e for e in activity if GR(e.get("gr")) != normalized_gr]
        cleared_count = original_count - len(activity)
        
        activity_file_path = os.path.join(DATA_DIR, "activity.json")
        with open(activity_file_path, "w") as f:
            json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True, 
            "message": f"Cleared {cleared_count} activity entries for {gr}",
            "cleared_count": cleared_count
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

#----------------------------------------------------------------------------------------------------------------------
#COMPLAINTS ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/complaints")
def complaints_page():
    gr = request.args.get("gr")
    
    if gr:
        complaint_rows = complaints_for(gr)
        order_list = orders_for(gr)  # Add this line to get orders data
    else:
        complaint_rows = []
        order_list = []  # Add this line for empty state
    
    return render_template(
        "complaints.html", 
        page="complaints", 
        **ctx_for(gr, complaint_rows=complaint_rows, orders=order_list)  # Pass orders to template
    )

#----------------------------------------------------------------------------------------------------------------------
#CREATE COMPLAINT ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/create_complaint", methods=["POST"])
def create_complaint():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        # Validate required fields
        required_fields = ['gr', 'order_id', 'recipe', 'description', 'compensation_type', 'compensation_amount']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        gr = data.get('gr')
        order_id = data.get('order_id')
        recipe = data.get('recipe')
        description = data.get('description')
        compensation_type = data.get('compensation_type')
        compensation_amount = data.get('compensation_amount')
        
        # Validate compensation type
        if compensation_type not in ['credit', 'refund']:
            return jsonify({"success": False, "error": "Invalid compensation type"}), 400
        
        # Validate compensation amount
        try:
            compensation_amount = float(compensation_amount)
            if compensation_amount < 0:
                return jsonify({"success": False, "error": "Compensation amount cannot be negative"}), 400
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Invalid compensation amount"}), 400
        
        # Verify the order exists and is committed
        target_order = None
        for order in orders:
            if order.get('order_id') == order_id and GR(order.get('gr')) == GR(gr):
                if order.get('status') != 'committed':
                    return jsonify({"success": False, "error": "Can only create complaints for committed orders"}), 400
                target_order = order
                break
        
        if not target_order:
            return jsonify({"success": False, "error": "Order not found or not accessible"}), 404
        
        # Verify the recipe exists in the order
        order_recipes = target_order.get('recipes', [])
        recipe_found = False
        for order_recipe in order_recipes:
            recipe_name = order_recipe.get('name') if isinstance(order_recipe, dict) else order_recipe
            if recipe_name == recipe:
                recipe_found = True
                break
        
        if not recipe_found:
            return jsonify({"success": False, "error": "Recipe not found in selected order"}), 400
        
        # Generate unique complaint ID
        import uuid
        complaint_id = f"COMP-{datetime.datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create new complaint
        new_complaint = {
            "complaint_id": complaint_id,
            "gr": gr,
            "order_id": order_id,
            "recipe": recipe,
            "issue": description,
            "compensation_type": compensation_type,
            "compensation": compensation_amount,
            "status": "resolved",  # Automatically resolved since compensation is being issued
            "date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_by": "admin"
        }
        
        # Add to complaints list
        complaints.append(new_complaint)
        
        # Save to file
        complaints_file_path = os.path.join(DATA_DIR, "complaints.json")
        with open(complaints_file_path, "w") as f:
            json.dump(complaints, f, indent=2)
        
        # Log activity
        activity_entry = {
            "gr": gr,
            "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "category": "complaint_created",
            "actor": "admin",
            "description": "Complaint logged",
            "detail": f"Complaint {complaint_id} created for order {order_id} - {compensation_type} of £{compensation_amount:.2f} issued"
        }
        activity.append(activity_entry)
        
        # Save activity log
        activity_file_path = os.path.join(DATA_DIR, "activity.json")
        with open(activity_file_path, "w") as f:
            json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Complaint created successfully",
            "complaint": new_complaint
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

#----------------------------------------------------------------------------------------------------------------------
#UPDATE COMPLAINT ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/update_complaint", methods=["POST"])
def update_complaint():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        complaint_id = data.get('complaint_id')
        gr = data.get('gr')
        
        if not complaint_id or not gr:
            return jsonify({"success": False, "error": "Missing complaint ID or GR"}), 400
        
        # Find the complaint
        complaint_found = False
        old_complaint = None
        
        for i, complaint in enumerate(complaints):
            if (complaint.get('complaint_id') == complaint_id and 
                GR(complaint.get('gr')) == GR(gr)):
                
                old_complaint = complaint.copy()
                
                # Update editable fields
                if 'description' in data:
                    complaints[i]['issue'] = data['description']
                if 'compensation_type' in data:
                    complaints[i]['compensation_type'] = data['compensation_type']
                if 'compensation_amount' in data:
                    complaints[i]['compensation'] = float(data['compensation_amount'])
                if 'status' in data:
                    complaints[i]['status'] = data['status']
                
                # Update modification timestamp
                complaints[i]['modified_date'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                complaints[i]['modified_by'] = 'admin'
                
                complaint_found = True
                updated_complaint = complaints[i]
                break
        
        if not complaint_found:
            return jsonify({"success": False, "error": "Complaint not found"}), 404
        
        # Save to file
        complaints_file_path = os.path.join(DATA_DIR, "complaints.json")
        with open(complaints_file_path, "w") as f:
            json.dump(complaints, f, indent=2)
        
        # Log activity
        activity_entry = {
            "gr": gr,
            "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "category": "complaint_updated",
            "actor": "admin",
            "description": "Complaint modified",
            "detail": f"Complaint {complaint_id} updated"
        }
        activity.append(activity_entry)
        
        # Save activity log
        activity_file_path = os.path.join(DATA_DIR, "activity.json")
        with open(activity_file_path, "w") as f:
            json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Complaint updated successfully",
            "complaint": updated_complaint
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

#----------------------------------------------------------------------------------------------------------------------
#DELETE COMPLAINT ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/delete_complaint", methods=["POST"])
def delete_complaint():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        complaint_id = data.get('complaint_id')
        gr = data.get('gr')
        
        if not complaint_id or not gr:
            return jsonify({"success": False, "error": "Missing complaint ID or GR"}), 400
        
        # Find and remove the complaint
        complaint_found = False
        deleted_complaint = None
        
        for i, complaint in enumerate(complaints):
            if (complaint.get('complaint_id') == complaint_id and 
                GR(complaint.get('gr')) == GR(gr)):
                
                deleted_complaint = complaint.copy()
                complaints.pop(i)
                complaint_found = True
                break
        
        if not complaint_found:
            return jsonify({"success": False, "error": "Complaint not found"}), 404
        
        # Save to file
        complaints_file_path = os.path.join(DATA_DIR, "complaints.json")
        with open(complaints_file_path, "w") as f:
            json.dump(complaints, f, indent=2)
        
        # Log activity
        activity_entry = {
            "gr": gr,
            "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "category": "complaint_deleted",
            "actor": "admin",
            "description": "Complaint deleted",
            "detail": f"Complaint {complaint_id} permanently removed (£{deleted_complaint.get('compensation', 0):.2f} {deleted_complaint.get('compensation_type', 'compensation')})"
        }
        activity.append(activity_entry)
        
        # Save activity log
        activity_file_path = os.path.join(DATA_DIR, "activity.json")
        with open(activity_file_path, "w") as f:
            json.dump(activity, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Complaint deleted successfully"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500
    
#----------------------------------------------------------------------------------------------------------------------
#MESSAGES ROUTE
#----------------------------------------------------------------------------------------------------------------------

@app.route("/messages")
def messages_page():
    gr = request.args.get("gr")
    print(f"DEBUG MESSAGES: GR parameter received: '{gr}'")  # Debug print
    
    if gr:
        normalized_gr = GR(gr)
        message_data = messages_by_gr.get(normalized_gr, {"log": []})
        print(f"DEBUG MESSAGES: Found {len(message_data.get('log', []))} messages for GR '{normalized_gr}'")  # Debug print
        print(f"DEBUG MESSAGES: Available GRs in messages: {list(messages_by_gr.keys())}")  # Debug print
    else:
        message_data = {"log": []}
    
    return render_template("messages.html", page="messages", 
                         **ctx_for(gr, messages=message_data))
#----------------------------------------------------------------------------------------------------------------------
#SANITY TEST - PING AND PEEKS
#----------------------------------------------------------------------------------------------------------------------

@app.route("/__ping")
def __ping():
    return "ok"

@app.route("/__datacheck")
def __datacheck():
    all_payments = get_all_payments()
    return {
        "customers": len(customers),
        "orders": len(orders),
        "complaints": len(complaints),
        "messages": len(messages),
        "subscriptions": len(subscriptions),
        "activity": len(activity),
        "generated_payments": len(all_payments)
    }

@app.route("/__peek_complaints")
def __peek_complaints():
    gr = GR(request.args.get("gr"))
    rows = complaints_for(gr)
    return {"gr": gr, "count": len(rows), "complaint_ids": [c.get("complaint_id") for c in rows]}

@app.route("/__peek_complaints_index")
def __peek_complaints_index():
    idx = {}
    for c in complaints:
        g = GR(c.get("gr"))
        idx[g] = idx.get(g, 0) + 1
    rows = [{"gr": k, "count": v} for k, v in sorted(idx.items())]
    return jsonify(rows)

if __name__ == "__main__":
    app.run(debug=True)





