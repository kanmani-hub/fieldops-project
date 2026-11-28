import sys
import datetime
from sqlalchemy import text
from app.database import SessionLocal
from app.models import Job, Organization, Technician, JobClosure, User
from app.models.enterprise_audit import EnterpriseAuditLog

def get_db():
    """Attempt database connection with graceful error handling."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        return db
    except Exception:
        print("\n⚠️  Unable to connect to PostgreSQL database.")
        print("👉 Please ensure your PostgreSQL service or Docker container is running.\n")
        return None

def format_dt(dt):
    """Format datetime in local timezone as YYYY-MM-DD HH:MM."""
    if not dt:
        return "-"
    try:
        if dt.tzinfo is not None:
            local_tz = datetime.datetime.now().astimezone().tzinfo
            dt = dt.astimezone(local_tz)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)[:16]

def format_dt_full(dt):
    """Format datetime with seconds."""
    if not dt:
        return "N/A"
    try:
        if dt.tzinfo is not None:
            local_tz = datetime.datetime.now().astimezone().tzinfo
            dt = dt.astimezone(local_tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)[:19]

def get_job_lifecycle_data(db, job):
    """Collect all lifecycle timestamps, technician info, and closure costs for a job."""
    tech = None
    if job.assigned_technician_id:
        tech = db.query(Technician).filter(Technician.technician_id == job.assigned_technician_id).first()
    
    if tech:
        tech_display = f"{tech.technician_name} (#{tech.technician_id})"
    elif job.assigned_technician_id:
        tech_display = f"#{job.assigned_technician_id}"
    else:
        tech_display = "Unassigned"

    logs = db.query(EnterpriseAuditLog).filter(
        EnterpriseAuditLog.entity_type == "job",
        EnterpriseAuditLog.entity_id == str(job.id)
    ).order_by(EnterpriseAuditLog.timestamp.asc()).all()

    closure = db.query(JobClosure).filter(JobClosure.job_id == job.id).first()

    created_at = job.created_at
    assigned_at = job.assigned_at
    
    accepted_at = next((l.timestamp for l in logs if "ACCEPTED" in (l.action or "").upper()), None)
    
    in_progress_at = (
        job.work_started_at 
        or job.on_site_at 
        or job.en_route_at 
        or next((l.timestamp for l in logs if "STARTED" in (l.action or "").upper() or (l.new_value and ("IN_PROGRESS" in str(l.new_value) or "ON_SITE" in str(l.new_value) or "EN_ROUTE" in str(l.new_value)))), None)
    )

    completed_at = job.completed_at or (closure.completed_at if closure else None)
    if not completed_at:
        completed_at = next((l.timestamp for l in logs if "COMPLETED" in (l.action or "").upper()), None)

    amount_str = "-"
    subtotal = None
    gst_amount = None
    total_amount = None

    if closure and closure.subtotal is not None:
        subtotal = float(closure.subtotal)
        gst_amount = round(subtotal * 0.05, 2)
        total_amount = round(subtotal + gst_amount, 2)
        amount_str = f"₹{total_amount:.2f}"

    return {
        "job": job,
        "tech_display": tech_display,
        "tech": tech,
        "created_at": created_at,
        "assigned_at": assigned_at,
        "accepted_at": accepted_at,
        "in_progress_at": in_progress_at,
        "completed_at": completed_at,
        "amount_str": amount_str,
        "subtotal": subtotal,
        "gst_amount": gst_amount,
        "total_amount": total_amount,
        "closure": closure,
        "logs": logs
    }

def print_command_suggestions():
    """Print standard suggestions footer in clean English."""
    print("💡 COMMAND SUGGESTIONS:")
    print("   • View specific job audit log:   python check_audit_logs.py <job_id>  (e.g. python check_audit_logs.py 40)")
    print("   • View force / manual assigns:   python check_audit_logs.py force")
    print("   • View full lifecycle timeline:  python check_audit_logs.py full")
    print("   • View real-time Kafka events:   python check_kafka_events.py\n")

def show_help():
    """Display CLI help guide."""
    print(f"\n{'='*30} FIELDOPS AUDIT LOGS INSPECTOR {'='*30}")
    print("Available Commands:")
    print("  python check_audit_logs.py             List recent 15 jobs with status and technician names")
    print("  python check_audit_logs.py <job_id>    View complete lifecycle timeline & audit events for a job")
    print("  python check_audit_logs.py force       List all jobs assigned manually or via admin force-assign")
    print("  python check_audit_logs.py full        Display expanded lifecycle progression for recent jobs")
    print("  python check_audit_logs.py help        Show this help message\n")

def show_recent_jobs(limit=15):
    db = get_db()
    if not db:
        return
    try:
        jobs = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
        print(f"\n{'='*45} RECENT {len(jobs)} JOBS {'='*45}")
        header = f"{'Job ID':<7} | {'Status':<11} | {'Service':<16} | {'Customer':<16} | {'Technician (Name & ID)':<24} | {'Created At':<16} | {'Completed At':<16} | {'Total Bill':<11}"
        print(header)
        print("-" * len(header))
        for j in jobs:
            data = get_job_lifecycle_data(db, j)
            c_time = format_dt(data["created_at"])
            comp_time = format_dt(data["completed_at"])
            print(f"#{j.id:<6} | {j.status:<11} | {j.service_type[:15]:<16} | {j.customer_name[:15]:<16} | {data['tech_display'][:23]:<24} | {c_time:<16} | {comp_time:<16} | {data['amount_str']:<11}")
        print("-" * len(header))
        print()
        print_command_suggestions()
    finally:
        db.close()

def show_force_assigned_jobs():
    db = get_db()
    if not db:
        return
    try:
        jobs = db.query(Job).filter(Job.assigned_by.isnot(None)).order_by(Job.id.desc()).all()
        print(f"\n{'='*30} FORCE / MANUALLY ASSIGNED JOBS ({len(jobs)}) {'='*30}")
        header = f"{'Job ID':<7} | {'Status':<11} | {'Assigned By (User)':<30} | {'Assigned To (Tech Name & ID)':<26} | {'Assigned At'}"
        print(header)
        print("-" * len(header))
        for j in jobs:
            user = db.query(User).filter(User.id == j.assigned_by).first()
            user_label = f"{user.email} ({user.role})" if user else j.assigned_by
            assigned_at = format_dt(j.assigned_at) if getattr(j, "assigned_at", None) else "N/A"
            
            tech = None
            if j.assigned_technician_id:
                tech = db.query(Technician).filter(Technician.technician_id == j.assigned_technician_id).first()
            
            if tech:
                tech_display = f"{tech.technician_name} (#{tech.technician_id})"
            elif j.assigned_technician_id:
                tech_display = f"#{j.assigned_technician_id}"
            else:
                tech_display = "Unassigned"

            print(f"#{j.id:<6} | {j.status:<11} | {user_label[:29]:<30} | {tech_display[:25]:<26} | {assigned_at}")
        print("-" * len(header))
        print()
        print_command_suggestions()
    finally:
        db.close()

def show_job_logs(job_id):
    db = get_db()
    if not db:
        return
    try:
        job = db.query(Job).filter(Job.id == int(job_id)).first()
        if not job:
            print(f"\n❌ Job #{job_id} not found in database.\n")
            return

        data = get_job_lifecycle_data(db, job)

        print(f"\n{'='*28} JOB #{job_id} FULL DETAILS {'='*28}")
        print(f"Customer:        {job.customer_name}")
        print(f"Service:         {job.service_type}")
        print(f"Status:          {job.status}")
        print(f"Technician:      {data['tech_display']}")
        
        # Assignment Details
        if job.assigned_by:
            user = db.query(User).filter(User.id == job.assigned_by).first()
            user_label = f"{user.email} ({user.role})" if user else job.assigned_by
            print(f"Assigned By:     {user_label}  <-- [MANUAL / FORCE ASSIGN]")
        else:
            print(f"Assigned By:     Auto-dispatch (System)")

        print("-" * 75)
        print("🕒 LIFECYCLE TIMESTAMPS:")
        print(f"  1. Job Created:  {format_dt_full(data['created_at'])}")
        print(f"  2. Assigned:     {format_dt_full(data['assigned_at'])}")
        print(f"  3. Accepted:     {format_dt_full(data['accepted_at'])}")
        print(f"  4. In Progress:  {format_dt_full(data['in_progress_at'])}")
        print(f"  5. Completed:    {format_dt_full(data['completed_at'])}")
        
        print("-" * 75)
        print("💰 BILLING & COSTS:")
        closure = data["closure"]
        if closure:
            print(f"  Service/Labour: ₹{closure.labour_cost:.2f}")
            print(f"  Material:       ₹{closure.material_cost:.2f}")
            print(f"  Subtotal:       ₹{data['subtotal']:.2f}")
            print(f"  GST (5%):       ₹{data['gst_amount']:.2f}")
            print(f"  Total Bill:     ₹{data['total_amount']:.2f}  <-- [SUBTOTAL + 5% GST]")
            if closure.work_summary:
                print(f"  Work Summary:   {closure.work_summary}")
        else:
            print(f"  Total Bill:     - (Job not yet closed/invoiced)")
        print("-" * 75)

        logs = data["logs"]
        print(f"\n{'='*25} AUDIT TIMELINE FOR JOB #{job_id} ({len(logs)} Events) {'='*25}")
        if not logs:
            print("No audit logs found for this job.")
        else:
            for l in logs:
                time_str = format_dt_full(l.timestamp)
                user = l.user_email or l.user_id or "System"
                print(f"[{time_str}] Action: {l.action:<25} | Role: {l.role or 'N/A':<12} | User: {user}")
                if l.new_value:
                    print(f"   -> Details: {l.new_value}")
        print("=" * 78 + "\n")
        print_command_suggestions()
    finally:
        db.close()

def show_full_recent_jobs(limit=15):
    """Show detailed lifecycle card for each recent job."""
    db = get_db()
    if not db:
        return
    try:
        jobs = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
        print(f"\n{'='*28} RECENT {len(jobs)} JOBS - FULL LIFECYCLE {'='*28}")
        for j in jobs:
            data = get_job_lifecycle_data(db, j)
            print(f"\n--- Job #{j.id} | {j.service_type} | Customer: {j.customer_name} ---")
            print(f"Status:      {j.status}")
            print(f"Technician:  {data['tech_display']}")
            print(f"Timeline:    Created: {format_dt(data['created_at'])}  ->  Assigned: {format_dt(data['assigned_at'])}  ->  Accepted: {format_dt(data['accepted_at'])}  ->  In-Progress: {format_dt(data['in_progress_at'])}  ->  Completed: {format_dt(data['completed_at'])}")
            if data["total_amount"] is not None:
                print(f"Total Bill:  ₹{data['total_amount']:.2f} (Subtotal: ₹{data['subtotal']:.2f} + GST 5%: ₹{data['gst_amount']:.2f})")
            else:
                print(f"Total Bill:  -")
        print("\n" + "=" * 78 + "\n")
        print_command_suggestions()
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("help", "-h", "--help"):
            show_help()
        elif arg in ("force", "overrides", "--force", "manual"):
            show_force_assigned_jobs()
        elif arg in ("full", "all", "--full", "lifecycle"):
            show_full_recent_jobs()
        elif arg.isdigit():
            show_job_logs(arg)
        else:
            show_recent_jobs()
    else:
        show_recent_jobs()
