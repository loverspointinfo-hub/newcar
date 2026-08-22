import requests
from bs4 import BeautifulSoup
import re
from flask import Flask, request, jsonify
from threading import Thread
from colorama import Fore, Style, init
import time

# Initialize colorama for colored output
init(autoreset=True)

# ===============================================
# FLASK APP SETUP
# ===============================================
app = Flask(__name__)

# ===============================================
# CONFIGURATION
# ===============================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
    "Referer": "https://vahanx.in/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br"
}

FLASK_PORT = 8888

# ===============================================
# ENHANCED VEHICLE INFO SCRAPER
# ===============================================
def get_comprehensive_vehicle_details(rc_number: str) -> dict:
    """Enhanced scraper combining both approaches for maximum detail extraction."""
    rc = rc_number.strip().upper()
    url = f"https://vahanx.in/rc-search/{rc}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        return {"error": f"Failed to fetch data: {str(e)}"}

    # Helper function to extract card values
    def extract_card(label):
        for div in soup.select(".hrcd-cardbody"):
            span = div.find("span")
            if span and label.lower() in span.text.lower():
                p = div.find("p")
                return p.get_text(strip=True) if p else None
        return None

    # Helper function to extract from sections
    def extract_from_section(header_text, keys):
        section = soup.find("h3", string=lambda s: s and header_text.lower() in s.lower())
        section_card = section.find_parent("div", class_="hrc-details-card") if section else None
        result = {}
        for key in keys:
            span = section_card.find("span", string=lambda s: s and key in s) if section_card else None
            if span:
                val = span.find_next("p")
                result[key.lower().replace(" ", "_")] = val.get_text(strip=True) if val else None
        return result

    # Generic value extractor
    def get_value(label):
        try:
            div = soup.find("span", string=label)
            if div:
                div = div.find_parent("div")
                p = div.find("p") if div else None
                return p.get_text(strip=True) if p else None
        except:
            return None

    # Extract registration number from h1
    try:
        registration_number = soup.find("h1").text.strip()
    except:
        registration_number = rc

    # Extract main card details
    modal_name = extract_card("Modal Name") or get_value("Model Name")
    owner_name = extract_card("Owner Name") or get_value("Owner Name")
    code = extract_card("Code")
    city = extract_card("City Name") or get_value("City Name")
    phone = extract_card("Phone") or get_value("Phone")
    website = extract_card("Website")
    address = extract_card("Address") or get_value("Address")

    # Extract ownership details
    ownership = extract_from_section("Ownership Details", [
        "Owner Name", "Father's Name", "Owner Serial No", "Registration Number", "Registered RTO"
    ])

    # Extract vehicle details
    vehicle = extract_from_section("Vehicle Details", [
        "Model Name", "Maker Model", "Vehicle Class", "Fuel Type", "Fuel Norms", 
        "Cubic Capacity", "Seating Capacity"
    ])

    # Extract insurance information
    insurance_expired_box = soup.select_one(".insurance-alert-box.expired .title")
    expired_days = None
    if insurance_expired_box:
        match = re.search(r"(\d+)", insurance_expired_box.text)
        expired_days = int(match.group(1)) if match else None
    
    insurance = extract_from_section("Insurance Information", [
        "Insurance Company", "Insurance No", "Insurance Expiry", "Insurance Upto"
    ])
    
    insurance_status = "Expired" if expired_days else "Active"
    
    # Extract important dates
    validity = extract_from_section("Important Dates", [
        "Registration Date", "Vehicle Age", "Fitness Upto", "Insurance Upto", 
        "Insurance Expiry In", "Tax Upto", "Tax Paid Upto"
    ])

    # Extract PUC details
    puc = extract_from_section("PUC Details", [
        "PUC No", "PUC Upto"
    ])

    # Extract other information
    other = extract_from_section("Other Information", [
        "Financer Name", "Financier Name", "Cubic Capacity", "Seating Capacity", 
        "Permit Type", "Blacklist Status", "NOC Details"
    ])

    # Compile comprehensive data
    data = {
        "registration_number": registration_number,
        "status": "success",
        "basic_info": {
            "model_name": modal_name,
            "owner_name": owner_name,
            "fathers_name": get_value("Father's Name") or ownership.get("father's_name"),
            "code": code,
            "city": city,
            "phone": phone,
            "website": website,
            "address": address
        },
        "ownership_details": {
            "owner_name": ownership.get("owner_name") or owner_name,
            "fathers_name": ownership.get("father's_name"),
            "serial_no": ownership.get("owner_serial_no") or get_value("Owner Serial No"),
            "rto": ownership.get("registered_rto") or get_value("Registered RTO")
        },
        "vehicle_details": {
            "maker": vehicle.get("model_name") or modal_name,
            "model": vehicle.get("maker_model") or get_value("Maker Model"),
            "vehicle_class": vehicle.get("vehicle_class") or get_value("Vehicle Class"),
            "fuel_type": vehicle.get("fuel_type") or get_value("Fuel Type"),
            "fuel_norms": vehicle.get("fuel_norms") or get_value("Fuel Norms"),
            "cubic_capacity": vehicle.get("cubic_capacity") or other.get("cubic_capacity"),
            "seating_capacity": vehicle.get("seating_capacity") or other.get("seating_capacity")
        },
        "insurance": {
            "status": insurance_status,
            "company": insurance.get("insurance_company") or get_value("Insurance Company"),
            "policy_number": insurance.get("insurance_no") or get_value("Insurance No"),
            "expiry_date": insurance.get("insurance_expiry") or get_value("Insurance Expiry"),
            "valid_upto": insurance.get("insurance_upto") or get_value("Insurance Upto"),
            "expired_days_ago": expired_days
        },
        "validity": {
            "registration_date": validity.get("registration_date") or get_value("Registration Date"),
            "vehicle_age": validity.get("vehicle_age") or get_value("Vehicle Age"),
            "fitness_upto": validity.get("fitness_upto") or get_value("Fitness Upto"),
            "insurance_upto": validity.get("insurance_upto") or get_value("Insurance Upto"),
            "insurance_status": validity.get("insurance_expiry_in"),
            "tax_upto": validity.get("tax_upto") or validity.get("tax_paid_upto") or get_value("Tax Upto")
        },
        "puc_details": {
            "puc_number": puc.get("puc_no") or get_value("PUC No"),
            "puc_valid_upto": puc.get("puc_upto") or get_value("PUC Upto")
        },
        "other_info": {
            "financer": other.get("financer_name") or other.get("financier_name") or get_value("Financier Name"),
            "permit_type": other.get("permit_type") or get_value("Permit Type"),
            "blacklist_status": other.get("blacklist_status") or get_value("Blacklist Status"),
            "noc": other.get("noc_details") or get_value("NOC Details")
        }
    }

    # Remove None values
    def clean_dict(d):
        if isinstance(d, dict):
            return {k: clean_dict(v) for k, v in d.items() if v is not None and v != ""}
        return d
    
    return clean_dict(data)

# ===============================================
# FLASK API ROUTES
# ===============================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Vehicle Information API",
        "version": "2.0",
        "endpoints": {
            "vehicle_info": "/api/vehicle-info?rc=<RC_NUMBER>",
            "health": "/health"
        },
        "example": f"http://localhost:{FLASK_PORT}/api/vehicle-info?rc=DL01AB1234"
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "api": "active",
        "timestamp": time.time()
    })

@app.route("/api/vehicle-info", methods=["GET"])
def get_vehicle_info():
    rc = request.args.get("rc")
    if not rc:
        return jsonify({"error": "Missing rc parameter", "usage": "/api/vehicle-info?rc=<RC_NUMBER>"}), 400

    try:
        data = get_comprehensive_vehicle_details(rc)
        if data.get("error"):
            return jsonify(data), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===============================================
# CONSOLE DISPLAY FUNCTIONS
# ===============================================
def print_banner():
    """Display application banner"""
    banner = f"""
{Fore.CYAN}╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║        🚗 VEHICLE INFORMATION API + CONSOLE 🚗            ║
║                                                           ║
║          Comprehensive RC Details Lookup System           ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
    """
    print(banner)

def display_vehicle_details(data):
    """Display vehicle details in a formatted way"""
    if data.get("error"):
        print(f"\n{Fore.RED}❌ ERROR: {data['error']}{Style.RESET_ALL}\n")
        return
    
    print(f"\n{Fore.GREEN}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}✅ VEHICLE DETAILS FOR: {data['registration_number']}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}{'='*60}{Style.RESET_ALL}\n")
    
    # Basic Information
    if data.get("basic_info"):
        print(f"{Fore.YELLOW}📋 BASIC INFORMATION:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        bi = data["basic_info"]
        if bi.get("owner_name"):
            print(f"  👤 Owner Name        : {Fore.WHITE}{bi['owner_name']}{Style.RESET_ALL}")
        if bi.get("fathers_name"):
            print(f"  👨 Father's Name     : {Fore.WHITE}{bi['fathers_name']}{Style.RESET_ALL}")
        if bi.get("model_name"):
            print(f"  🚗 Model Name        : {Fore.WHITE}{bi['model_name']}{Style.RESET_ALL}")
        if bi.get("city"):
            print(f"  🏙️  City              : {Fore.WHITE}{bi['city']}{Style.RESET_ALL}")
        if bi.get("phone"):
            print(f"  📞 Phone             : {Fore.WHITE}{bi['phone']}{Style.RESET_ALL}")
        if bi.get("address"):
            print(f"  📍 Address           : {Fore.WHITE}{bi['address']}{Style.RESET_ALL}")
        print()

    # Ownership Details
    if data.get("ownership_details"):
        print(f"{Fore.YELLOW}👥 OWNERSHIP DETAILS:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        od = data["ownership_details"]
        if od.get("owner_name"):
            print(f"  👤 Owner             : {Fore.WHITE}{od['owner_name']}{Style.RESET_ALL}")
        if od.get("fathers_name"):
            print(f"  👨 Father's Name     : {Fore.WHITE}{od['fathers_name']}{Style.RESET_ALL}")
        if od.get("serial_no"):
            print(f"  🔢 Serial Number     : {Fore.WHITE}{od['serial_no']}{Style.RESET_ALL}")
        if od.get("rto"):
            print(f"  🏢 Registered RTO    : {Fore.WHITE}{od['rto']}{Style.RESET_ALL}")
        print()

    # Vehicle Details
    if data.get("vehicle_details"):
        print(f"{Fore.YELLOW}🚙 VEHICLE SPECIFICATIONS:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        vd = data["vehicle_details"]
        if vd.get("maker"):
            print(f"  🏭 Maker             : {Fore.WHITE}{vd['maker']}{Style.RESET_ALL}")
        if vd.get("model"):
            print(f"  📦 Model             : {Fore.WHITE}{vd['model']}{Style.RESET_ALL}")
        if vd.get("vehicle_class"):
            print(f"  🏷️  Vehicle Class     : {Fore.WHITE}{vd['vehicle_class']}{Style.RESET_ALL}")
        if vd.get("fuel_type"):
            print(f"  ⛽ Fuel Type         : {Fore.WHITE}{vd['fuel_type']}{Style.RESET_ALL}")
        if vd.get("fuel_norms"):
            print(f"  🌱 Fuel Norms        : {Fore.WHITE}{vd['fuel_norms']}{Style.RESET_ALL}")
        if vd.get("cubic_capacity"):
            print(f"  🔧 Cubic Capacity    : {Fore.WHITE}{vd['cubic_capacity']}{Style.RESET_ALL}")
        if vd.get("seating_capacity"):
            print(f"  💺 Seating Capacity  : {Fore.WHITE}{vd['seating_capacity']}{Style.RESET_ALL}")
        print()

    # Insurance Details
    if data.get("insurance"):
        print(f"{Fore.YELLOW}🛡️  INSURANCE DETAILS:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        ins = data["insurance"]
        status_color = Fore.RED if ins.get("status") == "Expired" else Fore.GREEN
        print(f"  📊 Status            : {status_color}{ins.get('status', 'Unknown')}{Style.RESET_ALL}")
        if ins.get("company"):
            print(f"  🏢 Company           : {Fore.WHITE}{ins['company']}{Style.RESET_ALL}")
        if ins.get("policy_number"):
            print(f"  📄 Policy Number     : {Fore.WHITE}{ins['policy_number']}{Style.RESET_ALL}")
        if ins.get("expiry_date"):
            print(f"  📅 Expiry Date       : {Fore.WHITE}{ins['expiry_date']}{Style.RESET_ALL}")
        if ins.get("valid_upto"):
            print(f"  ✅ Valid Upto        : {Fore.WHITE}{ins['valid_upto']}{Style.RESET_ALL}")
        if ins.get("expired_days_ago"):
            print(f"  ⚠️  Expired           : {Fore.RED}{ins['expired_days_ago']} days ago{Style.RESET_ALL}")
        print()

    # Validity Information
    if data.get("validity"):
        print(f"{Fore.YELLOW}📅 VALIDITY INFORMATION:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        val = data["validity"]
        if val.get("registration_date"):
            print(f"  📆 Registration Date : {Fore.WHITE}{val['registration_date']}{Style.RESET_ALL}")
        if val.get("vehicle_age"):
            print(f"  ⏳ Vehicle Age       : {Fore.WHITE}{val['vehicle_age']}{Style.RESET_ALL}")
        if val.get("fitness_upto"):
            print(f"  ✅ Fitness Upto      : {Fore.WHITE}{val['fitness_upto']}{Style.RESET_ALL}")
        if val.get("insurance_upto"):
            print(f"  🛡️  Insurance Upto    : {Fore.WHITE}{val['insurance_upto']}{Style.RESET_ALL}")
        if val.get("tax_upto"):
            print(f"  💵 Tax Upto          : {Fore.WHITE}{val['tax_upto']}{Style.RESET_ALL}")
        print()

    # PUC Details
    if data.get("puc_details") and any(data["puc_details"].values()):
        print(f"{Fore.YELLOW}🔍 PUC DETAILS:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        puc = data["puc_details"]
        if puc.get("puc_number"):
            print(f"  📋 PUC Number        : {Fore.WHITE}{puc['puc_number']}{Style.RESET_ALL}")
        if puc.get("puc_valid_upto"):
            print(f"  📅 Valid Upto        : {Fore.WHITE}{puc['puc_valid_upto']}{Style.RESET_ALL}")
        print()

    # Other Information
    if data.get("other_info") and any(data["other_info"].values()):
        print(f"{Fore.YELLOW}ℹ️  OTHER INFORMATION:{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'─'*60}{Style.RESET_ALL}")
        oi = data["other_info"]
        if oi.get("financer"):
            print(f"  🏦 Financer          : {Fore.WHITE}{oi['financer']}{Style.RESET_ALL}")
        if oi.get("permit_type"):
            print(f"  📜 Permit Type       : {Fore.WHITE}{oi['permit_type']}{Style.RESET_ALL}")
        if oi.get("blacklist_status"):
            status_color = Fore.RED if "yes" in str(oi['blacklist_status']).lower() else Fore.GREEN
            print(f"  ⚠️  Blacklist Status  : {status_color}{oi['blacklist_status']}{Style.RESET_ALL}")
        if oi.get("noc"):
            print(f"  📄 NOC Details       : {Fore.WHITE}{oi['noc']}{Style.RESET_ALL}")
        print()

    print(f"{Fore.GREEN}{'='*60}{Style.RESET_ALL}\n")

# ===============================================
# FLASK RUNNER IN THREAD
# ===============================================
def run_flask():
    """Run Flask API in background"""
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)

# ===============================================
# CONSOLE INTERFACE
# ===============================================
def console_mode():
    """Console interface for vehicle lookup"""
    print_banner()
    
    print(f"{Fore.GREEN}✅ Flask API is running on: {Fore.CYAN}http://localhost:{FLASK_PORT}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}📡 API Endpoint: {Fore.CYAN}http://localhost:{FLASK_PORT}/api/vehicle-info?rc=<RC_NUMBER>{Style.RESET_ALL}\n")
    print(f"{Fore.YELLOW}{'─'*60}{Style.RESET_ALL}\n")
    
    while True:
        try:
            print(f"{Fore.CYAN}Enter vehicle registration number (or 'quit' to exit):{Style.RESET_ALL}")
            rc_number = input(f"{Fore.YELLOW}RC Number > {Style.RESET_ALL}").strip()
            
            if rc_number.lower() in ['quit', 'exit', 'q']:
                print(f"\n{Fore.CYAN}👋 Thank you for using Vehicle Information System!{Style.RESET_ALL}\n")
                break
            
            if not rc_number:
                print(f"{Fore.RED}❌ Please enter a valid RC number!{Style.RESET_ALL}\n")
                continue
            
            # Fetch and display vehicle details
            print(f"\n{Fore.YELLOW}🔍 Fetching data from vahanx.in...{Style.RESET_ALL}")
            details = get_comprehensive_vehicle_details(rc_number)
            print(f"{Fore.GREEN}✅ Data fetched successfully!{Style.RESET_ALL}")
            display_vehicle_details(details)
            
            # Ask if user wants to continue
            print(f"{Fore.CYAN}Do you want to search another vehicle? (yes/no):{Style.RESET_ALL}")
            choice = input(f"{Fore.YELLOW}Choice > {Style.RESET_ALL}").strip().lower()
            
            if choice not in ['yes', 'y', '']:
                print(f"\n{Fore.CYAN}👋 Thank ou for using Vehicle Information System!{Style.RESET_ALL}\n")
                break
            
            print("\n" + "="*60 + "\n")
            
        except KeyboardInterrupt:
            print(f"\n\n{Fore.YELLOW}⚠️  Interrupted by user{Style.RESET_ALL}")
            print(f"{Fore.CYAN}👋 Thank you for using Vehicle Information System!{Style.RESET_ALL}\n")
            break
        except Exception as e:
            print(f"\n{Fore.RED}❌ Unexpected error: {str(e)}{Style.RESET_ALL}\n")
            continue

# ===============================================
# MAIN FUNCTION
# ===============================================
def main():
    """Main application entry point"""
    try:
        # Start Flask API in background thread
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Give Flask a moment to start
        time.sleep(2)
        
        # Start console interface
        console_mode()
        
    except Exception as e:
        print(f"{Fore.RED}❌ Fatal error: {str(e)}{Style.RESET_ALL}")

if __name__ == "__main__":
    main()