"""Count specialty varieties by exact source type and stable collectible ID."""
from __future__ import annotations

from pathlib import Path

from .contracts import (Snapshot, assemble_result, canonical_bytes, evidence_location,
                        json_pointer, new_result, require)

COLLECTIBLES_SHA256 = 'e1f1bda7f30becc90ae4a1ecf1a254da74554464858c71dd5722998173656756'
SOURCE_PATH = 'content_data/collectibles.json'


def catalog_request(region=None):
    return {'tool': 'count_catalog', 'catalog': 'specialty', 'metric': 'item_count',
            'filters': {'region': region, 'type': None}}


def _validate_request(request):
    require(set(request) == {'tool', 'catalog', 'metric', 'filters'}, 'request_keys')
    require(request['tool'] == 'count_catalog', 'request_tool')
    filters = request['filters']
    require(isinstance(filters, dict) and set(filters) == {'region', 'type'}, 'filter_keys')
    require(all(v is None or (isinstance(v, str) and bool(v.strip())) for v in filters.values()), 'filter_value')


def _catalog_rows(data):
    require(isinstance(data, list), 'catalog_root_array')
    by_id, conflicts = {}, []
    for index, row in enumerate(data):
        require(isinstance(row, dict), 'catalog_row_object')
        require(all(isinstance(row.get(k), str) and row[k].strip() for k in ('采集物ID', '名称', '类型')), 'catalog_required_field')
        require(row['类型'].endswith('区域特产') and len(row['类型']) > 4, 'catalog_type')
        identity = row['采集物ID']
        if identity in by_id:
            prior_index, prior = by_id[identity]
            if canonical_bytes(prior) != canonical_bytes(row):
                conflicts.append({'input': identity, 'candidates': [json_pointer(prior_index), json_pointer(index)],
                                  'reason': '同ID的源记录内容不同，不能任选其一'})
        else:
            by_id[identity] = (index, row)
    return by_id, conflicts


def _selected_rows(result, rows, snapshot):
    region = result['request']['filters']['region']
    for identity, (index, row) in sorted(rows.items()):
        if region is not None and row['类型'] != region + '区域特产':
            continue
        eid = 'catalog:' + identity
        result['members'].append({'member_id': identity, 'display_name': row['名称'], 'evidence_ids': [eid]})
        result['evidence'].append({'evidence_id': eid, 'subject_id': identity, 'kind': 'catalog_field',
            'locations': [evidence_location(snapshot, json_pointer(index, key)) for key in ('采集物ID', '名称', '类型')],
            'member_checks': []})


def count_catalog(request, *, root=None, trusted_sha256=COLLECTIBLES_SHA256, empty_complete=False):
    """Only specialty/item_count is supported; all source failures are explicit errors.

    empty_complete is a caller's explicit complete-empty snapshot declaration;
    no empty declaration is inferred from a missing file or an empty array.
    """
    result = new_result(request)
    try:
        _validate_request(request)
        if request['catalog'] != 'specialty' or request['metric'] != 'item_count' or request['filters']['type'] is not None:
            result.update(status='unsupported', unsupported_reason='首批仅支持区域特产品种计数，不支持其它目录、系列或类型限定')
            return assemble_result(result)
        root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        snapshot = Snapshot.read(root, SOURCE_PATH, trusted_sha256)
        data = snapshot.data()
        rows, ambiguities = _catalog_rows(data)
        result['source_snapshots'] = [snapshot.identity()]
        if ambiguities:
            result.update(status='ambiguous', ambiguities=ambiguities)
            return assemble_result(result)
        require(bool(data) or empty_complete is True, 'empty_snapshot_without_completeness')
        total = len(data)
        result['denominator'].update(total_in_source=total, with_field=total, missing_field=0,
                                     considered=len(rows), with_authoritative_data=len(rows), unresolved=0)
        _selected_rows(result, rows, snapshot)
        result.update(status='complete' if result['members'] else 'zero', count_basis='exact_in_snapshot')
        return assemble_result(result, snapshots={snapshot.identity()['source_id']: snapshot})
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
        error = new_result(request, status='error', gaps=[type(exc).__name__ + ': ' + str(exc)])
        return assemble_result(error)
