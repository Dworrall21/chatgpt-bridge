from chatgpt_bridge.hermes.token_accounting import TokenLedger, compute_savings


def test_savings_formula():
    ledger = TokenLedger(
        router_input=100, router_output=50,
        provider_input=300, provider_output=200,
        result_ingestion_input=80, result_ingestion_output=40,
        native_subagent_input=2000, native_subagent_output=1500,
        native_result_ingestion=300,
    )
    r = compute_savings(ledger)
    assert r.actual_hermes_tokens == 770
    assert r.baseline_hermes_tokens == 3800
    assert r.net_saved == 3030
    assert abs(r.savings_percent - 79.7) < 0.1


def test_zero_baseline():
    r = compute_savings(TokenLedger())
    assert r.savings_percent is None


def test_baseline_method_propagates():
    r = compute_savings(TokenLedger(native_subagent_input=100), baseline_method="matched_history", confidence="low")
    assert r.baseline_method == "matched_history"
    assert r.confidence == "low"
