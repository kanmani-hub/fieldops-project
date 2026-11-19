import sys
import json
import subprocess

TOPIC_SHORTCUTS = {
    "job": "fieldops.job.events",
    "jobs": "fieldops.job.events",
    "gps": "fieldops.gps.events",
    "notification": "fieldops.notification.events",
    "notifications": "fieldops.notification.events",
    "billing": "fieldops.billing.events",
    "payment": "fieldops.payment.events",
    "sla": "fieldops.sla.events",
    "audit": "fieldops.audit.events",
    "events": "fieldops.events",
}

ALL_TOPICS = [
    ("fieldops.job.events", "Job lifecycle (Created, Assigned, Accepted, In-Progress, Completed)"),
    ("fieldops.gps.events", "Technician live GPS coordinates and location pings"),
    ("fieldops.notification.events", "SMS, Push, and Email notifications sent to Techs/Customers"),
    ("fieldops.billing.events", "Job invoices, labor & material cost closures"),
    ("fieldops.payment.events", "Payment transactions and verification events"),
    ("fieldops.sla.events", "SLA monitoring, countdown warnings, and escalations"),
    ("fieldops.audit.events", "Enterprise security and operational audit streams"),
    ("fieldops.events", "Core system events stream"),
]

def check_docker_kafka_status():
    """Verify whether Docker Desktop and the fieldops-kafka container are running."""
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", "name=fieldops-kafka", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if "fieldops-kafka" not in res.stdout:
            print("⚠️  Docker Desktop is not running or 'fieldops-kafka' container is not active.")
            print("👉 Please start Docker Desktop and run: docker compose up -d\n")
            return False
        return True
    except Exception:
        print("⚠️  Unable to connect to Docker engine. Please ensure Docker Desktop is running.\n")
        return False

def lookup_db_info(job_id=None, tech_id=None):
    """
    Query database to enrich Kafka event with Customer Name, Service Type, 
    Current Job Status, and Technician Name.
    """
    job_info = {}
    tech_info = {}
    db = None
    try:
        from app.database import SessionLocal
        from app.models import Job, Technician
        db = SessionLocal()

        # Lookup Job Details
        if job_id:
            try:
                clean_job_id = int(str(job_id).replace("#", "").strip())
                job = db.query(Job).filter(Job.id == clean_job_id).first()
                if job:
                    job_info["customer_name"] = job.customer_name
                    job_info["service_type"] = job.service_type
                    job_info["status"] = job.status
                    job_info["priority"] = job.priority
                    if not tech_id and job.assigned_technician_id:
                        tech_id = job.assigned_technician_id
            except Exception:
                pass

        # Lookup Technician Details
        if tech_id:
            try:
                clean_tech_id = str(tech_id).replace("#", "").strip()
                tech = None
                if clean_tech_id.isdigit():
                    tech = db.query(Technician).filter(Technician.technician_id == int(clean_tech_id)).first()
                if not tech:
                    tech = db.query(Technician).filter(Technician.tech_id == clean_tech_id).first()
                if tech:
                    tech_info["name"] = tech.technician_name
                    tech_info["id"] = tech.technician_id
            except Exception:
                pass
    except Exception:
        # Fallback silently if database is unreachable
        pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass

    return job_info, tech_info

def generate_event_message(event_type, topic, job_id, customer, service, tech_display, payload):
    """Generate a friendly, clear English explanation of the event message."""
    direct_msg = payload.get("message") or payload.get("description") or payload.get("body") or payload.get("title")
    if direct_msg and isinstance(direct_msg, str):
        return direct_msg

    evt_norm = str(event_type).lower().replace("-", "_").replace(" ", "_")

    if "assign" in evt_norm:
        if tech_display and tech_display != "Unassigned":
            return f"Job #{job_id} assigned to Technician {tech_display}"
        return f"Job #{job_id} assigned to a technician"
    elif "create" in evt_norm or "new" in evt_norm:
        if customer and service:
            return f"New service request for '{service}' submitted by {customer} (Job #{job_id})"
        elif service:
            return f"New service request for '{service}' created (Job #{job_id})"
        elif job_id:
            return f"New Job #{job_id} created in system"
        return "New service job created"
    elif "accept" in evt_norm:
        if tech_display and tech_display != "Unassigned":
            return f"Technician {tech_display} accepted Job #{job_id}"
        return f"Job #{job_id} accepted by technician"
    elif "progress" in evt_norm or "start" in evt_norm or "route" in evt_norm:
        if tech_display and tech_display != "Unassigned":
            return f"Technician {tech_display} started work on Job #{job_id}"
        return f"Job #{job_id} is currently in progress"
    elif "complet" in evt_norm:
        if tech_display and tech_display != "Unassigned":
            return f"Job #{job_id} successfully completed by {tech_display}"
        return f"Job #{job_id} marked as COMPLETED"
    elif "cancel" in evt_norm:
        return f"Job #{job_id} has been cancelled"
    elif "gps" in topic or "location" in evt_norm:
        lat = payload.get("latitude") or payload.get("lat")
        lng = payload.get("longitude") or payload.get("lng")
        coord_str = f" at ({lat}, {lng})" if lat and lng else ""
        if tech_display and tech_display != "Unassigned":
            return f"Live GPS location ping from Technician {tech_display}{coord_str}"
        return f"Technician live GPS location ping{coord_str}"
    elif "billing" in topic or "invoice" in evt_norm:
        inv_id = payload.get("invoice_id")
        amt = payload.get("amount") or payload.get("total_amount")
        inv_str = f" #{inv_id}" if inv_id else ""
        amt_str = f" for ₹{amt}" if amt else ""
        return f"Billing invoice{inv_str} generated{amt_str} for Job #{job_id}"
    elif "payment" in topic:
        amt = payload.get("amount") or payload.get("total_amount")
        amt_str = f" of ₹{amt}" if amt else ""
        return f"Payment transaction processed{amt_str} for Job #{job_id}"
    elif "notification" in topic:
        channel = payload.get("channel", "Notification")
        recip = payload.get("recipient") or payload.get("phone") or payload.get("email") or ""
        recip_str = f" to {recip}" if recip else ""
        return f"{channel.upper()} notification alert dispatched{recip_str}"
    elif "sla" in topic:
        return f"SLA monitoring event for Job #{job_id}"
    elif "audit" in topic:
        action = payload.get("action", event_type)
        return f"Enterprise security audit recorded: {action}"

    if job_id:
        return f"System event '{event_type}' processed for Job #{job_id}"
    return f"System event '{event_type}' processed successfully"

def show_topics():
    """Print the available Kafka topics, shortcuts, and usage guide in English."""
    print(f"\n{'='*32} FIELDOPS KAFKA TOPICS {'='*33}")
    print(f"{'Topic Name':<32} | {'Shortcut':<14} | {'Description'}")
    print("-" * 92)
    for topic, desc in ALL_TOPICS:
        shortcut = [k for k, v in TOPIC_SHORTCUTS.items() if v == topic][0]
        print(f"{topic:<32} | {shortcut:<14} | {desc}")
    print("-" * 92)
    print()
    print("💡 USAGE EXAMPLES:")
    print("   • View clean summary cards:   python check_kafka_events.py job")
    print("   • View with raw JSON payload:  python check_kafka_events.py job --raw")
    print("   • Specify number of events:    python check_kafka_events.py job 10")
    print("   • Check billing events:        python check_kafka_events.py billing")
    print("   • Check technician GPS pings:  python check_kafka_events.py gps\n")

def print_clean_event(idx, raw_line, show_raw=False):
    """Format and display a Kafka message in an intuitive, fully enriched card format."""
    raw_line = raw_line.strip()
    if not raw_line or not raw_line.startswith("{"):
        return
    try:
        data = json.loads(raw_line)
    except Exception:
        print(f"\n[Raw Event #{idx}]: {raw_line}\n")
        return

    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

    topic = data.get("topic") or "N/A"
    msg_id = data.get("message_id") or payload.get("event_id") or data.get("id") or "N/A"
    timestamp = data.get("timestamp") or payload.get("timestamp") or "N/A"
    correlation_id = data.get("correlation_id") or "N/A"

    event_type = (
        payload.get("event_type")
        or data.get("message_type")
        or payload.get("action")
        or "EVENT"
    ).upper()

    sender = data.get("sender")
    sender_str = "N/A"
    if isinstance(sender, dict):
        agent_type = sender.get("agent_type", "")
        agent_id = sender.get("agent_id", "")
        sender_str = f"{agent_type} ({agent_id})" if agent_id else agent_type
    elif sender:
        sender_str = str(sender)

    # Contextual fields extracted from Kafka payload
    job_id = payload.get("job_id") or data.get("job_id")
    tech_id = payload.get("technician_id") or payload.get("assigned_technician_id")
    customer = payload.get("customer_name") or payload.get("customer")
    service_type = payload.get("service_type") or payload.get("service")
    status = payload.get("status") or payload.get("new_status")
    action = payload.get("action")

    # Enrich missing data from PostgreSQL database
    db_job, db_tech = lookup_db_info(job_id=job_id, tech_id=tech_id)

    if not customer and db_job.get("customer_name"):
        customer = db_job["customer_name"]
    if not service_type and db_job.get("service_type"):
        service_type = db_job["service_type"]
    if not status and db_job.get("status"):
        status = db_job["status"]

    # Technician Name Resolution
    tech_name = None
    if db_tech.get("name"):
        tech_name = db_tech["name"]
        tech_id = db_tech.get("id") or tech_id
    elif payload.get("technician_name"):
        tech_name = payload.get("technician_name")

    if tech_name and tech_id:
        tech_display = f"{tech_name} (#{tech_id})"
    elif tech_name:
        tech_display = tech_name
    elif tech_id:
        tech_display = f"#{tech_id}"
    else:
        tech_display = None

    # Billing / Cost fields
    invoice_id = payload.get("invoice_id")
    amount = payload.get("amount") or payload.get("total_amount")
    currency = payload.get("currency") or "₹"

    # GPS fields
    lat = payload.get("latitude") or payload.get("lat")
    lng = payload.get("longitude") or payload.get("lng")

    # Notification fields
    recipient = payload.get("recipient") or payload.get("phone") or payload.get("email")
    channel = payload.get("channel")
    notification_content = payload.get("body") or payload.get("message") or payload.get("template")

    # Generate friendly, human-readable Message explanation
    event_message = generate_event_message(
        event_type=event_type,
        topic=topic,
        job_id=job_id,
        customer=customer,
        service=service_type,
        tech_display=tech_display,
        payload=payload
    )

    border = "=" * 76
    print(f"\n{border}")
    print(f"📌 EVENT #{idx}: [{event_type}]")
    print("-" * 76)
    print(f"  • Message:         {event_message}")
    print(f"  • Topic:           {topic}")
    print(f"  • Timestamp:       {timestamp}")
    print(f"  • Message ID:      {msg_id}")
    if sender_str != "N/A":
        print(f"  • Source / Sender: {sender_str}")
    if correlation_id != "N/A":
        print(f"  • Correlation ID:  {correlation_id}")

    # Core details section
    details = []
    if job_id:
        details.append(f"  • Job ID:          #{job_id}")
    if customer:
        details.append(f"  • Customer Name:   {customer}")
    if service_type:
        details.append(f"  • Service Type:    {service_type}")
    if tech_display:
        details.append(f"  • Technician:      {tech_display}")
    if status:
        details.append(f"  • Status:          {status}")
    if action:
        details.append(f"  • Action:          {action}")
    if invoice_id:
        details.append(f"  • Invoice ID:      {invoice_id}")
    if amount is not None:
        try:
            details.append(f"  • Amount:          {currency}{float(amount):.2f}")
        except Exception:
            details.append(f"  • Amount:          {currency}{amount}")
    if lat is not None and lng is not None:
        details.append(f"  • Coordinates:     {lat}, {lng}")
    if recipient:
        details.append(f"  • Recipient:       {recipient} ({channel or 'Notification'})")
    if notification_content and isinstance(notification_content, str):
        details.append(f"  • Content:         {notification_content}")

    if details:
        print("\n📋 DETAILS:")
        for line in details:
            print(line)

    if show_raw:
        print("\n📦 RAW JSON PAYLOAD:")
        print(json.dumps(data, indent=2))

    print(border)

def read_topic_history(topic_name, limit=5, show_raw=False):
    """Fetch messages from the specified Kafka topic and render them."""
    print(f"\n🔍 Fetching last {limit} events from topic: '{topic_name}'...")

    cmd = [
        "docker", "exec", "fieldops-kafka",
        "/opt/kafka/bin/kafka-console-consumer.sh",
        "--bootstrap-server", "localhost:9092",
        "--topic", topic_name,
        "--from-beginning",
        "--max-messages", str(limit),
        "--timeout-ms", "5000",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        lines = [l for l in proc.stdout.strip().split("\n") if l.strip().startswith("{")]
    except subprocess.TimeoutExpired as te:
        stdout = te.stdout.decode("utf-8", errors="ignore") if isinstance(te.stdout, bytes) else (te.stdout or "")
        lines = [l for l in stdout.strip().split("\n") if l.strip().startswith("{")]
    except Exception as e:
        print(f"❌ Error connecting to Kafka container: {e}")
        return

    if not lines:
        if topic_name == "fieldops.billing.events":
            print(f"ℹ️  No messages in '{topic_name}'. Checking domain topic 'fieldops.events' for invoices...")
            return read_topic_history("fieldops.events", limit=limit, show_raw=show_raw)

        print(f"\nℹ️  No messages found in topic '{topic_name}' yet.")
        print("   (Events will appear as actions occur in the application).\n")
        print("💡 COMMAND SUGGESTIONS:")
        print(f"   • To list all available topics:    python check_kafka_events.py")
        print(f"   • To check database audit logs:   python check_audit_logs.py\n")
        return

    for idx, line in enumerate(lines, 1):
        print_clean_event(idx, line, show_raw=show_raw)

    print(f"\n✅ Total {len(lines)} event(s) fetched successfully.")
    print("-" * 76)
    print("💡 COMMAND SUGGESTIONS:")
    if not show_raw:
        print(f"   • View full raw JSON:             python check_kafka_events.py {topic_name} --raw")
    print(f"   • Fetch custom number of events:  python check_kafka_events.py {topic_name} 10")
    print(f"   • List all available topics:      python check_kafka_events.py\n")

def main():
    args = sys.argv[1:]
    if not args:
        show_topics()
        return

    show_raw = False
    clean_args = []
    for arg in args:
        if arg.lower() in ("--raw", "-r", "raw"):
            show_raw = True
        else:
            clean_args.append(arg)

    if not clean_args:
        show_topics()
        return

    first_arg = clean_args[0].lower()

    if first_arg in ("topics", "list", "--list", "help", "-h", "--help"):
        show_topics()
        return

    if first_arg == "live":
        topic_key = clean_args[1].lower() if len(clean_args) > 1 else "job"
        topic_name = TOPIC_SHORTCUTS.get(topic_key, topic_key)
        limit = int(clean_args[2]) if len(clean_args) > 2 and clean_args[2].isdigit() else 5
        read_topic_history(topic_name, limit=limit, show_raw=show_raw)
        return

    topic_name = TOPIC_SHORTCUTS.get(first_arg, first_arg)
    limit = 5
    if len(clean_args) > 1 and clean_args[1].isdigit():
        limit = int(clean_args[1])

    read_topic_history(topic_name, limit=limit, show_raw=show_raw)

if __name__ == "__main__":
    main()
