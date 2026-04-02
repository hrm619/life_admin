# ANSI color codes
BOLD = "\033[1m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

URGENCY_COLORS = {"high": RED, "medium": YELLOW, "low": GREEN}
URGENCY_LABELS = {"high": "HIGH", "medium": "MED", "low": "LOW"}


def format_briefing(briefing: dict) -> str:
    lines = []
    lines.append(f"\n{BOLD}{'=' * 50}")
    lines.append(f"  MORNING BRIEFING")
    lines.append(f"{'=' * 50}{RESET}\n")

    action_items = briefing.get("action_required", [])
    info_items = briefing.get("informational", [])
    schedule_items = briefing.get("schedule", [])
    task_items = briefing.get("tasks", [])

    if not action_items and not info_items and not schedule_items and not task_items:
        lines.append(f"  {DIM}No new items since last briefing.{RESET}\n")
        return "\n".join(lines)

    # Action Required
    if action_items:
        lines.append(f"{BOLD}{RED}  ACTION REQUIRED{RESET}")
        lines.append(f"  {DIM}{'─' * 40}{RESET}")
        sorted_items = sorted(
            action_items,
            key=lambda x: ["high", "medium", "low"].index(x.get("urgency", "low")),
        )
        for item in sorted_items:
            urgency = item.get("urgency", "low")
            color = URGENCY_COLORS.get(urgency, GREEN)
            label = URGENCY_LABELS.get(urgency, "LOW")
            people = ", ".join(item.get("people", []))
            source = item.get("source", "")
            source_tag = f" {DIM}({source}){RESET}" if source else ""
            lines.append(f"  {color}[{label}]{RESET} {BOLD}{item['summary']}{RESET}{source_tag}")
            if people:
                lines.append(f"        {DIM}from: {people}{RESET}")
            lines.append(f"        {item['detail']}")
            lines.append("")

    # Schedule
    if schedule_items:
        lines.append(f"{BOLD}{GREEN}  SCHEDULE{RESET}")
        lines.append(f"  {DIM}{'─' * 40}{RESET}")
        current_date = None
        for item in schedule_items:
            item_date = item.get("date", "")
            if item_date and item_date != current_date:
                current_date = item_date
                try:
                    from datetime import datetime
                    dt = datetime.strptime(item_date, "%Y-%m-%d")
                    day_name = dt.strftime("%A")
                    lines.append(f"  {DIM}{day_name}, {item_date}{RESET}")
                except ValueError:
                    lines.append(f"  {DIM}{item_date}{RESET}")
            if item.get("all_day"):
                time_str = "ALL DAY"
            else:
                time_str = item.get("time", "")
            lines.append(f"  {GREEN}[{time_str}]{RESET} {BOLD}{item['title']}{RESET}")
            location = item.get("location", "")
            if location:
                lines.append(f"        {DIM}{location}{RESET}")
            lines.append("")

    # Tasks (from Notes)
    if task_items:
        lines.append(f"{BOLD}{YELLOW}  TASKS{RESET}")
        lines.append(f"  {DIM}{'─' * 40}{RESET}")
        for item in task_items:
            source_note = item.get("source_note", "")
            note_tag = f" {DIM}(from: {source_note}){RESET}" if source_note else ""
            lines.append(f"  {YELLOW}•{RESET} {BOLD}{item['title']}{RESET}{note_tag}")
            detail = item.get("detail", "")
            if detail:
                lines.append(f"        {detail}")
            lines.append("")

    # Informational
    if info_items:
        lines.append(f"{BOLD}{CYAN}  INFORMATIONAL{RESET}")
        lines.append(f"  {DIM}{'─' * 40}{RESET}")
        for item in info_items:
            lines.append(f"  {CYAN}•{RESET} {BOLD}{item['summary']}{RESET}")
            lines.append(f"        {item['detail']}")
            lines.append("")

    return "\n".join(lines)
