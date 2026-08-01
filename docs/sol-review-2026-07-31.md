ChatGPT said:
Conclusion

The bridge has several fail-open paths that can permit unintended sends, bypass content controls, expose results, or lose project confinement. Most seriously, feature flags are not enforced at the side-effect boundary, while recovery evidence is insufficient to determine whether a send occurred safely.

Key rationale / Findings

CRITICAL — queue.py, server.py: bridge_enabled and allow_conversation_creation are never enforced; allow_send=true alone activates CDP and may create conversations even when the bridge or creation flag is off.
Fix: Enforce all required flags immediately before every creation/send side effect using the daemon’s loaded configuration.

CRITICAL — server.py, security/content_policy.py: ContentPolicy.check() returns PolicyResult(False, ...), but the server ignores the result and only catches exceptions, so rejected secrets and oversized content are accepted.
Fix: Call apply_content_policy() or explicitly reject whenever result.allowed is false before any persistence.

HIGH — server.py: GET delegation results and POST cancellation perform no peer-UID, HMAC, timestamp, or nonce verification; any process able to access the socket can read results or cancel work.
Fix: Authenticate and authorize every non-public endpoint, signing the exact method, normalized path, body hash, and request identity.

HIGH — peer_auth.py, server.py: NonceGuard is unsynchronized under a threaded server, forgets all nonces at capacity, loses state on restart, and is constructed without the configured replay window; require_peer_uid also imposes no UID restriction when allowed_peer_uid is unset.
Fix: Use a locked, bounded persistent TTL nonce store that rejects saturation and require an explicit expected UID.

HIGH — queue.py, driver.py, project_guard.py: Requests are marked PROJECT_VERIFIED without invoking ProjectGuard; account binding is unchecked, and an existing thread is opened by globally matching its ID without proving current project membership.
Fix: Verify enrolled project ID, account fingerprint, mode, and thread containment immediately before composer fill and again before send.

HIGH — driver.py, cdp_client.py, config.py: CDP endpoints/origins are configurable without shown loopback or identity enforcement, while the production driver does not validate the Electron bridge/app identity and the arbitrary-evaluation flag is unused.
Fix: Restrict CDP to an authenticated local endpoint, pin target identity/build/origin on every connection, and expose only narrow allowlisted operations.

HIGH — queue.py, registry.py: The journal records SENT and WAITING before send_and_wait() performs the UI send, and no user-message ID is captured; after a crash, recovery cannot distinguish “not sent” from “sent.”
Fix: Persist intent, perform the click, capture immutable UI send evidence, then atomically mark SENT; reconcile ambiguous states read-only before any retry.

HIGH — server.py, queue.py: Cancellation only changes database state: queued/running jobs remain active and can still send, after which illegal transitions from CANCELLED can crash the worker path; completed jobs also do not repump their session queue.
Fix: Dequeue pending jobs, add cooperative cancellation checks immediately before send, use conditional state transitions, and repump after every job.

HIGH — registry.py: Prompts and results are always stored plaintext, database permissions are not explicitly hardened, and session_id_hash stores the first 32 raw session-ID characters rather than a hash.
Fix: Create storage with mode 0600, store only HMAC-derived identifiers, encrypt or minimize content, and enforce retention deletion.

MEDIUM — server.py, registry.py, output_gate.py: Protocol guarantees are inconsistent: deadlines are parsed but never enforced, sent_at is never set because its condition is impossible, and OutputGate is unused while responses claim untruncated/full retention.
Fix: Validate timezone-aware future deadlines, correct timestamp transitions, and route every result through the configured output gate before storage and response.

Risks / uncertainties

This source-only review is limited by the omitted signing implementation, request schema, CDP client, daemon startup, and filesystem deployment configuration; those components could add controls or reveal further weaknesses.