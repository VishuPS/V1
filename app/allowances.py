"""Free credits are lifetime; paid allowance fields retain their billing semantics."""
def used(record):
    return record.free_credits_used if record.plan_code == "FREE" else record.monthly_calls_used
def limit(record):
    return 250 if record.plan_code == "FREE" else record.monthly_call_limit
def end(record):
    return None if record.plan_code == "FREE" else record.usage_period_end
def start(record):
    return (record.free_credits_started_at or record.usage_period_start) if record.plan_code == "FREE" else record.usage_period_start
