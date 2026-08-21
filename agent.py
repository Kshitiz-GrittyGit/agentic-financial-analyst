import os
import requests
from dotenv import load_dotenv
from datetime import datetime
from anthropic import Anthropic
from helpers import get_financial_fact, PeriodError
import json
import time
from search import search_filings
load_dotenv()  # reads .env into os.environ

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


tools_schema = [
    {
        "name": "calculator",
        "description": "Evaluates a math expression and returns the numeric result.",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. '15*23'"}},
            "required": ["expression"],
        },
    },
    {
        "name": "get_financial_fact",
        "description": """Returns one exact financial figure from SEC XBRL data, with full source lineage. 
        Use for ALL numeric values (revenue, R&D expense, net income). 
        One call returns one metric for one company for one fiscal year.""",
        "input_schema": {
            "type": "object", 
            "properties": {"company":{"type": "string", "description": "e.g. 'AAPL'"},
        'metric':{"type": "string", "description": "e.g. 'revenue, reasearch and development, R&D'"}, 'period':{"type": "string", "description": "e.g. 'FY2024'"}},
        "required": ["company", 'metric', 'period']},
    },
    {"name": "search_filings",
            "description": """Searches the narrative text of a company's 10-K and returns relevant passages with 
            section breadcrumbs. Use for qualitative questions strategy, reasoning, risks, management commentary. 
            Do NOT use to obtain numeric figures; use get_financial_fact for those.""",
            "input_schema": 
            {"type": "object",
            "properties": {
                "company":  {"type": "string", "description": "Ticker, e.g. 'AAPL'"},
                "question": {"type": "string", "description": "Qualitative question, e.g. 'why is the company increasing R&D investment'"},
                "period":   {"type": "string", "description": "Fiscal year, e.g. 'FY2024'"},
    },
    "required": ["company", "question", "period"],
},
        }
]

system = (
    "You are a reasoning agent. Before every tool call, first explain your reasoning "
    "in one short sentence — what you're about to do and why. Then make the tool call. "
    "Work step by step, using tool results to decide your next step."
    """Also this is very important , don't use the ungrounded numbers from your core memory and only use numbers
    been extracted from the tools availbale. if you are unable to get desired result from tools fail loudly"""
)

def calculator(expression):
    return eval(str(expression))



TOOLS = {
    "calculator": calculator,
    "get_financial_fact": get_financial_fact,
    'search_filings':search_filings
}

def make_plan(task, model="claude-haiku-4-5"):
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        system="""You are a planning agent. Given a task, output a short numbered plan of steps. Do not execute anything — just plan.
        please note that you have a 3 tools avalable one for getting the correct facts of a company and another is to get 
        the narrative of a company and another is the calculator""",
        messages=[{"role": "user", "content": f"Task: {task}"}],
    )
    return resp.content[0].text

DETERMINISTIC = (PeriodError, ValueError)          # bad arguments — retry changes nothing
TRANSIENT = (requests.RequestException,)           # network, rate limit — worth retrying

def run_tool_with_retry(func, args, max_retries=2):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = func(**args)
            if isinstance(result, (dict, list)):
                return json.dumps(result), True
            return str(result), True
        except DETERMINISTIC as e:
            return f"ERROR: {e}", False             # straight back to the model
        except TRANSIENT as e:
            last_error = str(e)
            time.sleep(2 ** attempt)               # 1s, 2s, 4s
    return f"Tool failed after {max_retries + 1} attempts: {last_error}", False


def context_size(messages):
    return sum(len(str(m)) for m in messages) 


task = """Compare Apple's and Microsoft's R&D as a percentage of revenue for FY2023 and FY2024. 
Which company's ratio grew faster? Then summarize each company's stated strategic reasoning for its R&D investment, 
citing the filing sections."""
plan = make_plan(task)

print("📋 PLAN:\n" + plan + "\n")

messages=[{"role": "user", "content": f"Task: {task}\n\nHere is your plan:\n{plan}\n\nNow execute it step by step using your tools."}]


def multiturn_chat(tools,system,messages, model="claude-haiku-4-5", max_tokens=1500):
    
    counter = 20
    while counter>0:
        resp = client.messages.create(
                model=model,   # cheap for drills; swap if you like
                max_tokens=max_tokens,
                messages=messages,
                tools=tools,
                system=system
                )
        agent_response = resp.content
        messages.append({"role": "assistant", "content": agent_response})

        if resp.stop_reason == "tool_use":
            tool_result = []
            for block in agent_response:
                if block.type == "text":
                    print(f"  🧠 THOUGHT: {block.text}")
                elif block.type == "tool_use":
                    print(f"  🔧 ACTION: {block.name}({block.input})")
                
                    func = TOOLS.get(block.name)
                    if func is None:
                        result, ok = f"Error: unknown tool '{block.name}'", False
                    else:
                        result, ok = run_tool_with_retry(func, block.input)

                    marker = "👁️ " if ok else "❌"
                    print(f"  {marker} OBSERVATION: {result}")
            
            
                    tool_result.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result)
                        })
            
            messages.append({"role": "user", "content": tool_result}) 
            print(f"  📏 context: {context_size(messages)} chars, {len(messages)} messages")

        else:
            final = next(b.text for b in resp.content if b.type == "text")
            print("FINAL:", final)
            break
        
        counter-=1
    return messages


# inside your loop, after appending:
print(f"  📏 context: {context_size(messages)} chars, {len(messages)} messages")

msgs = multiturn_chat(tools_schema, system, messages)
