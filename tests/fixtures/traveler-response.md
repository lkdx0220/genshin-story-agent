---
{
  "schema_version": "0.2.0-draft",
  "entity_id": "char:traveler",
  "entity_type": "character",
  "data_mode": "fixture",
  "page_status": "draft",
  "as_of_version": null,
  "snapshot": {
    "game_version": "5.0",
    "story_event_id": null,
    "position": "unknown",
    "identity_version_id": "identity:traveler"
  },
  "canonical_name": "旅行者",
  "aliases": [
    "旅行者"
  ],
  "element": null,
  "weapon": null,
  "nation": null,
  "affiliation": [],
  "player_choice_options": [
    {
      "choice_id": "aether",
      "label": "空",
      "aliases": [
        "空",
        "哥哥"
      ]
    },
    {
      "choice_id": "lumine",
      "label": "荧",
      "aliases": [
        "荧",
        "妹妹"
      ]
    }
  ],
  "field_assertions": [
    {
      "path": "/canonical_name",
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:2a89bb4e22e45f76b89581f6",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": []
    },
    {
      "path": "/element",
      "status": "unknown",
      "evidence": [],
      "conflict_ids": []
    },
    {
      "path": "/weapon",
      "status": "unknown",
      "evidence": [],
      "conflict_ids": []
    },
    {
      "path": "/nation",
      "status": "unknown",
      "evidence": [],
      "conflict_ids": []
    },
    {
      "path": "/affiliation",
      "status": "unknown",
      "evidence": [],
      "conflict_ids": []
    },
    {
      "path": "/aliases/0",
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:2a89bb4e22e45f76b89581f6",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": []
    }
  ],
  "identity_versions": [
    {
      "identity_version_id": "identity:traveler",
      "label": "旅行者",
      "predecessor_ids": [],
      "transition_event_id": null,
      "version": {
        "introduced_in": null,
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "story_scope": {
        "label": null,
        "from_event_id": null,
        "from_boundary": "unknown",
        "to_event_id": null,
        "to_boundary": "unknown"
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:2a89bb4e22e45f76b89581f6",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "observed_at": [],
      "conflict_ids": [],
      "names": [
        {
          "name_id": "name:traveler",
          "value": "旅行者",
          "kind": "primary",
          "version": {
            "introduced_in": null,
            "valid_from": null,
            "valid_to": null,
            "verified_in": []
          },
          "story_scope": {
            "label": null,
            "from_event_id": null,
            "from_boundary": "unknown",
            "to_event_id": null,
            "to_boundary": "unknown"
          },
          "status": "candidate",
          "evidence": [
            {
              "source_span_id": "span:2a89bb4e22e45f76b89581f6",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "observed_at": [],
          "conflict_ids": [],
          "player_choice_scope": [],
          "player_choice_proof_refs": [
            "proof:fixture-span-62a943cc2e3ceb619c074f31"
          ]
        },
        {
          "name_id": "name:aether",
          "value": "空",
          "kind": "alias",
          "version": {
            "introduced_in": null,
            "valid_from": null,
            "valid_to": null,
            "verified_in": []
          },
          "story_scope": {
            "label": null,
            "from_event_id": null,
            "from_boundary": "unknown",
            "to_event_id": null,
            "to_boundary": "unknown"
          },
          "status": "candidate",
          "evidence": [
            {
              "source_span_id": "span:e254050d399f11317b294c92",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "observed_at": [],
          "conflict_ids": [],
          "player_choice_scope": [
            "aether"
          ],
          "player_choice_branch": {
            "choice_id": "aether",
            "kind": "source_metadata",
            "source_span_id": "span:c715eab56494ad6c81e884cb"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-c715eab56494ad6c81e884cb"
          ]
        },
        {
          "name_id": "name:lumine",
          "value": "荧",
          "kind": "alias",
          "version": {
            "introduced_in": null,
            "valid_from": null,
            "valid_to": null,
            "verified_in": []
          },
          "story_scope": {
            "label": null,
            "from_event_id": null,
            "from_boundary": "unknown",
            "to_event_id": null,
            "to_boundary": "unknown"
          },
          "status": "candidate",
          "evidence": [
            {
              "source_span_id": "span:ec5b918c3707fcc03c974e6c",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "observed_at": [],
          "conflict_ids": [],
          "player_choice_scope": [
            "lumine"
          ],
          "player_choice_branch": {
            "choice_id": "lumine",
            "kind": "source_metadata",
            "source_span_id": "span:f0fe3f851f05142f08ea3b4b"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-f0fe3f851f05142f08ea3b4b"
          ]
        }
      ]
    }
  ],
  "events": [
    {
      "event_id": "event:common",
      "event_entity_id": null,
      "title": "共同旅行",
      "description": "旅行者是在提瓦特旅行的主角。",
      "role": "participant",
      "identity_version_ids": [
        "identity:traveler"
      ],
      "version": {
        "introduced_in": "5.0",
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "story_time": {
        "label": null,
        "after_event_ids": [],
        "before_event_ids": []
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:2a89bb4e22e45f76b89581f6",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [],
      "player_choice_scope": [],
      "player_choice_proof_refs": [
        "proof:fixture-span-62a943cc2e3ceb619c074f31"
      ]
    },
    {
      "event_id": "event:aether",
      "event_entity_id": null,
      "title": "空的风",
      "description": "空使用风元素。",
      "role": "participant",
      "identity_version_ids": [
        "identity:traveler"
      ],
      "version": {
        "introduced_in": "5.0",
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "story_time": {
        "label": null,
        "after_event_ids": [],
        "before_event_ids": []
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:e254050d399f11317b294c92",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [],
      "player_choice_scope": [
        "aether"
      ],
      "player_choice_branch": {
        "choice_id": "aether",
        "kind": "source_metadata",
        "source_span_id": "span:c715eab56494ad6c81e884cb"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-c715eab56494ad6c81e884cb"
      ]
    },
    {
      "event_id": "event:lumine",
      "event_entity_id": null,
      "title": "荧的岩",
      "description": "荧使用岩元素。",
      "role": "participant",
      "identity_version_ids": [
        "identity:traveler"
      ],
      "version": {
        "introduced_in": "5.0",
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "story_time": {
        "label": null,
        "after_event_ids": [],
        "before_event_ids": []
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:ec5b918c3707fcc03c974e6c",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [],
      "player_choice_scope": [
        "lumine"
      ],
      "player_choice_branch": {
        "choice_id": "lumine",
        "kind": "source_metadata",
        "source_span_id": "span:f0fe3f851f05142f08ea3b4b"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-f0fe3f851f05142f08ea3b4b"
      ]
    }
  ],
  "relations": [],
  "memory_records": [],
  "affiliation_states": [],
  "conflicts": [],
  "test_refs": []
}
---
# 旅行者

> 候选页；来源内容尚未人工核验。

## 身份与归属
- 旅行者。[^source:https://example.invalid/traveler#p002]
- 名称：旅行者。[^source:https://example.invalid/traveler#p002]
- 名称：空（选择：空）。[^source:https://example.invalid/traveler#p004]
- 名称：荧（选择：荧）。[^source:https://example.invalid/traveler#p006]

## 时间线
- [版本 5.0] 共同旅行：旅行者是在提瓦特旅行的主角。[^source:https://example.invalid/traveler#p002]
- [版本 5.0] 空的风（选择：空）：空使用风元素。[^source:https://example.invalid/traveler#p004]
- [版本 5.0] 荧的岩（选择：荧）：荧使用岩元素。[^source:https://example.invalid/traveler#p006]

## 关系网络

## 争议与冲突
- [数据缺口] 未建立可核验的互斥断言；不推断不存在争议。

[^source:https://example.invalid/traveler#p002]: [原文](https://example.invalid/traveler#p002) — span:2a89bb4e22e45f76b89581f6
[^source:https://example.invalid/traveler#p004]: [原文](https://example.invalid/traveler#p004) — span:e254050d399f11317b294c92
[^source:https://example.invalid/traveler#p006]: [原文](https://example.invalid/traveler#p006) — span:ec5b918c3707fcc03c974e6c
