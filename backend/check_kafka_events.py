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
    """Check if Docker and the fieldops-kafka container are reachable."""
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", "name=fieldops-kafka", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if "fieldops-kafka" not in res.stdout:
            print("⚠️  Docker Desktop run aagala / 'fieldops-kafka' container running-la illa mama.")
            print("👉 Docker Desktop-ah start pannitu try pannunga (docker compose up -d)!\n")
            return False
        return True
    except Exception:
        print("⚠️  Docker Desktop detect aagala mama. Make sure Docker Desktop is running!\n")
        return False

def show_topics():
    print(f"\n{'='*30} FIELDOPS KAFKA TOPICS {'='*30}")
    print(f"{'Topic Name':<32} | {'Shortcut':<12} | {'Description'}")
    print("-" * 90)
    for topic, desc in ALL_TOPICS:
        shortcut = [k for k, v in TOPIC_SHORTCUTS.items() if v == topic][0]
        print(f"{topic:<32} | {shortcut:<12} | {desc}")
    print("-" * 90)
    print()
    print("💡 If you want to see Specific topic JSON events 👇🏻 :")
    print()
    print("   python check_kafka_events.py job")
    print("   python check_kafka_events.py billing")
    print("   python check_kafka_events.py gps")
    print("   python check_kafka_events.py job 10    (Fetch last 10 events)\n")

def print_formatted_event(idx, raw_line):
    raw_line = raw_line.strip()
    if not raw_line or not raw_line.startswith("{"):
        return
    try:
        data = json.loads(raw_line)
        topic = data.get("topic", "N/A")
        msg_id = data.get("message_id", "N/A")
        ts = data.get("timestamp", "N/A")
        corr_id = data.get("correlation_id", "N/A")
        
        payload = data.get("payload", {})
        event_type = payload.get("event_type") or data.get("message_type", "EVENT")
        job_id = payload.get("job_id", "N/A")
        tech_id = payload.get("technician_id", "N/A")
        
        print(f"\n{'='*25} EVENT #{idx} [{event_type.upper()}] {'='*25}")
        print(f"Topic:          {topic}")
        print(f"Message ID:     {msg_id}")
        print(f"Timestamp:      {ts}")
        print(f"Correlation ID: {corr_id}")
        if job_id != "N/A":
            print(f"Job ID:         {job_id}")
        if tech_id != "N/A":
            print(f"Technician ID:  {tech_id}")
        print("-" * 70)
        print("📦 JSON PAYLOAD:")
        print(json.dumps(data, indent=2))
        print("=" * 70)
    except Exception:
        print(raw_line)

def read_topic_history(topic_name, limit=5):
    print(f"\n🔍 Fetching last {limit} events from topic: '{topic_name}'...\n")
    
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
        print(f"ℹ️  No messages found in topic '{topic_name}' yet.")
        print("   (Events will appear as actions are performed in the app).")
        return
    
    for idx, line in enumerate(lines, 1):
        print_formatted_event(idx, line)
    print(f"\n✅ Total {len(lines)} messages fetched successfully.\n")

def main():
    args = sys.argv[1:]
    if not args:
        show_topics()
        return

    first_arg = args[0].lower()
    
    if first_arg in ("topics", "list", "--list", "help", "-h"):
        show_topics()
        return
        
    # If user passes 'live', let them know live streaming was removed and fetch history instead
    if first_arg == "live":
        topic_key = args[1].lower() if len(args) > 1 else "job"
        topic_name = TOPIC_SHORTCUTS.get(topic_key, topic_key)
        limit = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
        read_topic_history(topic_name, limit=limit)
        return

    topic_name = TOPIC_SHORTCUTS.get(first_arg, first_arg)
    limit = 5
    if len(args) > 1 and args[1].isdigit():
        limit = int(args[1])
        
    read_topic_history(topic_name, limit=limit)

if __name__ == "__main__":
    main()
