from pocketflow import Flow

from nodes import (
    DisplayBriefingNode,
    FetchCalendarNode,
    FetchGmailNode,
    FetchIMessageNode,
    FetchNotesNode,
    FollowUpAgentNode,
    LoadLastRunNode,
    SummarizeBriefingNode,
)


def create_flow() -> Flow:
    load = LoadLastRunNode()
    fetch_imsg = FetchIMessageNode(max_retries=2)
    fetch_cal = FetchCalendarNode(max_retries=2)
    fetch_gmail = FetchGmailNode(max_retries=2)
    fetch_notes = FetchNotesNode(max_retries=2)
    summarize = SummarizeBriefingNode(max_retries=3)
    display = DisplayBriefingNode()
    agent = FollowUpAgentNode(max_retries=2)

    # Briefing pipeline
    load >> fetch_imsg >> fetch_cal >> fetch_gmail >> fetch_notes >> summarize >> display >> agent

    # Agent loop — routes back to self on actions
    agent - "answer" >> agent
    agent - "draft_reply" >> agent
    agent - "draft_email" >> agent
    agent - "create_task" >> agent
    agent - "refresh" >> load
    # "done" has no successor — flow ends

    return Flow(start=load)
