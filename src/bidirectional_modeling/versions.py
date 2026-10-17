"""Independent format versions. A package release never implies proof migration."""

SESSION_SCHEMA_VERSION = 2
FINITE_MODEL_SCHEMA_VERSION = 1
REPLAY_SCHEMA_VERSION = 1
CORE_CONTRACT_REVISION = 4

# These are identity domains, not a generic certificate serialization schema.
REPLAY_IDENTITY_DOMAINS = {
    'context': 'context-v2',
    'observations': 'observed-model-v3',
    'batch': 'trace-batch-v3',
    'declaration': 'model-declaration-v1',
    'protocol': 'search-protocol-v1',
    'problem': 'search-problem-v2',
    'query': 'search-query-v1',
}
