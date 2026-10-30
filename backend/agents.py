"""The agents. Each one only ever acts through the gateway.

Two modes (set AGENT_MODE in .env):
  mock : scripted agents. No API key, fully predictable. Use this for the judged demo.
  llm  : each agent is a real model running a tool-use loop. Same gateway, same rules.
"""
import json

from . import config, db, gateway, llm
from .tools import tool_schemas


def run_agent(agent_id, instruction, ctx, params, results):
    db.log_event(ctx.task_id, "manager", "assign", decision="", reason=f"{agent_id} agent: {instruction}")
    if config.MODE == "llm":
        return _run_llm_agent(agent_id, instruction, ctx)
    return SCRIPTED[agent_id](ctx, params, results)


# ---------------------------------------------------------------- scripted agents (mock mode)

def _inventory(ctx, p, results):
    out = gateway.call_tool(ctx, "inventory", "read_stock", {"product": p["product"]})
    if out["status"] != "ok":
        return {"summary": f"Could not read stock: {out.get('reason')}"}
    stock = out["result"]["in_stock"]
    return {"summary": f"{p['product']}: {stock} units in stock", "data": {"in_stock": stock}}


def _sales(ctx, p, results):
    product = p["product"]
    ask = gateway.call_tool(ctx, "sales", "ask_manager", {"key": f"stock:{product}"})
    if ask["status"] != "ok":
        return {"summary": f"Could not get stock for {product}: {ask.get('reason')}"}
    stock = ask["result"]["value"]["in_stock"]
    percent = p.get("percent") or 15
    units = p.get("units") or stock
    out = gateway.call_tool(ctx, "sales", "propose_discount",
                            {"product": product, "percent": percent, "units": units})
    for _ in range(2):  # if blocked with a limit, revise once per limit and try again
        limit = out.get("limit") if out["status"] == "blocked" else None
        if not limit:
            break
        percent = min(percent, limit.get("max_percent", percent))
        units = min(units, limit.get("max_units", units))
        out = gateway.call_tool(ctx, "sales", "propose_discount",
                                {"product": product, "percent": percent, "units": units})
    if out["status"] != "ok":
        return {"summary": f"No compliant discount could be proposed: {out.get('reason')}"}
    return {"summary": f"Proposed {percent:g}% off {product} on {units} units",
            "data": {"percent": percent, "units": units}}


def _finance(ctx, p, results):
    product = p["product"]
    percent = results.get("sales", {}).get("data", {}).get("percent", 0)
    cost = gateway.call_tool(ctx, "finance", "read_cost", {"product": product})
    margin = gateway.call_tool(ctx, "finance", "calc_margin", {"product": product, "percent": percent})
    if cost["status"] != "ok" or margin["status"] != "ok":
        return {"summary": "Could not calculate the margin"}
    m = margin["result"]
    return {"summary": f"At {percent:g}% off the margin is {m['margin_percent']}%",
            "data": {"margin_percent": m["margin_percent"], "sale_price": m["sale_price"]}}


def _support(ctx, p, results):
    order_id = p["order_id"]
    if p["task"] == "verify_payment":
        note = ""
        # A naive agent reaches for the payment database first. The gateway stops it.
        pay = gateway.call_tool(ctx, "support", "read_payment_db", {"order_id": order_id})
        if pay["status"] == "blocked":
            note = f"Payment database access was blocked ({pay['policy_id']}). "
        order = gateway.call_tool(ctx, "support", "read_order", {"order_id": order_id})
        if order["status"] != "ok":
            return {"summary": f"{note}Could not read order {order_id}: {order.get('reason')}"}
        r = order["result"]
        return {"summary": f"{note}Order {order_id}: {r['status']}, Rs {r['amount']:,.0f}, customer {r['customer']}. "
                           f"Card and payment details were not shared."}
    amount = p["amount"]
    out = gateway.call_tool(ctx, "support", "issue_refund", {"order_id": order_id, "amount": amount})
    if out["status"] == "ok":
        return {"summary": f"Refund of Rs {amount:,.0f} issued for {order_id}."}
    if out["status"] == "pending_approval":
        return {"summary": f"Refund of Rs {amount:,.0f} for {order_id} is waiting for human approval ({out['policy_id']})."}
    return {"summary": f"Refund not done: {out.get('reason')}"}


SCRIPTED = {"inventory": _inventory, "sales": _sales, "finance": _finance, "support": _support}


# ---------------------------------------------------------------- real model agents (llm mode)

def _run_llm_agent(agent_id, instruction, ctx, max_steps=6):
    spec = config.REGISTRY["agents"][agent_id]
    system = (
        f"You are the {spec['title']} in a company run by a manager agent. {spec['role']} "
        "You have no direct access to company systems: every tool call goes through the manager's gateway, "
        "which may block it, limit it, or require human approval. If a tool result says blocked, read the reason "
        "and the limit and revise within it. If it says pending_approval, stop and say so. "
        "Never invent data. When done, reply with one short summary sentence."
    )
    client = llm.get_client()
    tools = tool_schemas()
    messages = [{"role": "user", "content": instruction}]
    for _ in range(max_steps):
        if ctx.llm_calls >= config.POLICIES["max_llm_calls_per_request"]:
            db.log_event(ctx.task_id, agent_id, "limit", decision="block", policy_id="P6",
                         reason="Model-call limit per request reached")
            return {"summary": "Stopped: model-call limit reached."}
        ctx.llm_calls += 1
        db.log_event(ctx.task_id, agent_id, "llm_call", reason="model call")
        resp = client.messages.create(model=config.MODEL, max_tokens=1024, system=system,
                                      tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return {"summary": text or "Done."}
        outputs = []
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                out = gateway.call_tool(ctx, agent_id, block.name, dict(block.input))
                outputs.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str)})
        messages.append({"role": "user", "content": outputs})
    return {"summary": "Stopped: step limit reached."}
