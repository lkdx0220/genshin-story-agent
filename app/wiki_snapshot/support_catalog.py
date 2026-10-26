"""Finite reviewed record digests; update only after a new semantic review."""
from types import MappingProxyType
from collections import namedtuple
Support = namedtuple("Support", "entity_id section id_key sha256 phase")
# None explicitly means no subject loss/recovery phase applies.
# The backup record describes Nahida holding a backup, not Wanderer
# experiencing either stage. Event records are outside this selector.
CATALOG = MappingProxyType({
    'memory:wanderer-memory-loss': Support('char:wanderer', 'memory_records', 'memory_record_id', '5e58dd27ba71a4ed4f3a411b872f708e1224430c61381e8de3cef94adad8138c', 'before_recovery'),
    'memory:nahida-backup': Support('char:wanderer', 'memory_records', 'memory_record_id', '6b91c5657f2a2150cfdf1d1f7a9a6fab389868cc172f1c58e63bc403274c1049', None),
    'memory:wanderer-regain': Support('char:wanderer', 'memory_records', 'memory_record_id', '0436da5805efed9b6628474bedfefcb7dd02e18c89756d5a5d6610756723177b', 'after_recovery'),
    'event:nahida-record-creation-report': Support('char:nahida', 'events', 'event_id', '78d84d9b77b81032fe5c9f9fc1b820b2e95cc8d026afeb40cd53dada3068023e', None),
    'event:nahida-memory-storage-report': Support('char:nahida', 'events', 'event_id', '718d442108a33465438a6edfde006973f83e64e3580afa1d6cd7d71db94ec5dc', None),
    'event:nahida-supervision-request': Support('char:nahida', 'events', 'event_id', 'a3d92d43cd57cdb4237a5a63cce5c814e31e8b831c7cd261ad08b52e58c24596', None),
    'event:nahida-memory-return': Support('char:nahida', 'events', 'event_id', 'cbcf57d92a67a0000e0fd23e0ae5074aa7fc43ac72f6e64a229a79cca6218548', None),
})
