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
    print("   • View clean summary cards:  python check_kafka_events.py job")
    print("   • View with raw JSON payload: python check_kafka_events.py job --raw")
    print("   • Specify number of events:   python check_kafka_events.py job 10")
    print("   • Check billing events:       python check_kafka_events.py billing")
    print("   • Check technician GPS pings: python check_kafka_events.py gps\n")

def print_clean_event(idx, raw_line, show_raw=False):
    """Format and display a Kafka message in a human-readable clean card format."""
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

    # Contextual fields
    job_id = payload.get("job_id") or data.get("job_id")
    tech_id = payload.get("technician_id") or payload.get("assigned_technician_id")
    status = payload.get("status") or payload.get("new_status")
    action = payload.get("action")
    service_type = payload.get("service_type") or payload.get("service")
    customer = payload.get("customer_name") or payload.get("customer")
    
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

    border = "=" * 76
    print(f"\n{border}")
    print(f"📌 EVENT #{idx}: [{event_type}]")
    print("-" * 76)
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
    if service_type:
        details.append(f"  • Service Type:    {service_type}")
    if customer:
        details.append(f"  • Customer:        {customer}")
    if tech_id:
        details.append(f"  • Technician ID:   #{tech_id}")
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
