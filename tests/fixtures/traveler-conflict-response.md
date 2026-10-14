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
  "affiliation": [
    "org:traveler"
  ],
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
          "source_span_id": "span:7654af987c350552db259209",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": []
    },
    {
      "path": "/element",
      "status": "disputed",
      "evidence": [],
      "conflict_ids": [
        "conflict:element"
      ]
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
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:7654af987c350552db259209",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": []
    },
    {
      "path": "/aliases/0",
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:7654af987c350552db259209",
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
          "source_span_id": "span:7654af987c350552db259209",
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
              "source_span_id": "span:7654af987c350552db259209",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "observed_at": [],
          "conflict_ids": [],
          "player_choice_scope": [],
          "player_choice_proof_refs": [
            "proof:fixture-span-b7138b253f2142467eae6a13"
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
              "source_span_id": "span:be31670b07a2f290b7538287",
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
            "source_span_id": "span:c50dd21b1b3186f074809cf6"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-c50dd21b1b3186f074809cf6"
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
              "source_span_id": "span:613fa3bb6af7f4aa78b06a34",
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
            "source_span_id": "span:58eb6f7063411e443d38fcf8"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-58eb6f7063411e443d38fcf8"
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
          "source_span_id": "span:7654af987c350552db259209",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [],
      "player_choice_scope": [],
      "player_choice_proof_refs": [
        "proof:fixture-span-b7138b253f2142467eae6a13"
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
          "source_span_id": "span:be31670b07a2f290b7538287",
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
        "source_span_id": "span:c50dd21b1b3186f074809cf6"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-c50dd21b1b3186f074809cf6"
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
          "source_span_id": "span:613fa3bb6af7f4aa78b06a34",
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
        "source_span_id": "span:58eb6f7063411e443d38fcf8"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-58eb6f7063411e443d38fcf8"
      ]
    }
  ],
  "relations": [],
  "memory_records": [],
  "affiliation_states": [
    {
      "state_id": "affiliation-state:traveler-affiliation",
      "identity_version_ids": [
        "identity:traveler"
      ],
      "state": "affiliated",
      "organization_ids": [
        "org:traveler"
      ],
      "coverage": {
        "extent": "named_organizations",
        "organization_ids": [
          "org:traveler"
        ],
        "source_ids": [
          "source:traveler-conflict"
        ],
        "reviewed_sources": 1,
        "authoritative_sources": 0,
        "completeness": "partial",
        "limitations": null
      },
      "observed_at": [
        {
          "game_version": "5.0",
          "story_event_id": null,
          "position": "unknown",
          "identity_version_id": "identity:traveler",
          "player_choice": null
        }
      ],
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
          "source_span_id": "span:7654af987c350552db259209",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [],
      "player_choice_scope": [],
      "player_choice_proof_refs": [
        "proof:fixture-span-b7138b253f2142467eae6a13"
      ]
    }
  ],
  "conflicts": [
    {
      "conflict_id": "conflict:element",
      "kind": "source_disagreement",
      "target_ref": "attribute:attr:traveler-element",
      "summary": "共同岩元素原值与空的风元素条件值并存",
      "status": "open",
      "player_choice_scope": [
        "aether"
      ],
      "player_choice_branch": {
        "choice_id": "aether",
        "kind": "source_metadata",
        "source_span_id": "span:c50dd21b1b3186f074809cf6"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-b7138b253f2142467eae6a13",
        "proof:fixture-span-c50dd21b1b3186f074809cf6"
      ],
      "alternatives": [
        {
          "alternative_id": "alt-original",
          "statement": "共同原值在空分支下保持岩元素",
          "proposed_value": {
            "attribute_id": "attr:traveler-element",
            "key": "element",
            "value": "geo",
            "value_label": null,
            "identity_version_ids": [
              "identity:traveler"
            ],
            "story_scope": {
              "label": null,
              "from_event_id": null,
              "from_boundary": "unknown",
              "to_event_id": null,
              "to_boundary": "unknown"
            },
            "version": {
              "introduced_in": null,
              "valid_from": null,
              "valid_to": null,
              "verified_in": []
            },
            "status": "candidate",
            "evidence": [
              {
                "source_span_id": "span:7654af987c350552db259209",
                "basis": "explicit_text",
                "authority": "non_authoritative"
              }
            ],
            "conflict_ids": [
              "conflict:element"
            ],
            "player_choice_scope": [
              "aether"
            ],
            "player_choice_proof_refs": [
              "proof:fixture-span-b7138b253f2142467eae6a13"
            ],
            "observed_at": [],
            "player_choice_branch": {
              "choice_id": "aether",
              "kind": "source_metadata",
              "source_span_id": "span:b7138b253f2142467eae6a13"
            }
          },
          "version": {
            "introduced_in": null,
            "valid_from": null,
            "valid_to": null,
            "verified_in": []
          },
          "evidence": [
            {
              "source_span_id": "span:7654af987c350552db259209",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "player_choice_scope": [
            "aether"
          ],
          "player_choice_branch": {
            "choice_id": "aether",
            "kind": "source_metadata",
            "source_span_id": "span:b7138b253f2142467eae6a13"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-b7138b253f2142467eae6a13"
          ]
        },
        {
          "alternative_id": "alt-new",
          "statement": "空使用风元素",
          "proposed_value": {
            "attribute_id": "attr:traveler-element",
            "key": "element",
            "value": "anemo",
            "value_label": null,
            "identity_version_ids": [
              "identity:traveler"
            ],
            "story_scope": {
              "label": null,
              "from_event_id": null,
              "from_boundary": "unknown",
              "to_event_id": null,
              "to_boundary": "unknown"
            },
            "version": {
              "introduced_in": null,
              "valid_from": null,
              "valid_to": null,
              "verified_in": []
            },
            "status": "candidate",
            "evidence": [
              {
                "source_span_id": "span:be31670b07a2f290b7538287",
                "basis": "explicit_text",
                "authority": "non_authoritative"
              }
            ],
            "conflict_ids": [
              "conflict:element"
            ],
            "player_choice_scope": [
              "aether"
            ],
            "player_choice_branch": {
              "choice_id": "aether",
              "kind": "source_metadata",
              "source_span_id": "span:c50dd21b1b3186f074809cf6"
            },
            "player_choice_proof_refs": [
              "proof:fixture-span-c50dd21b1b3186f074809cf6"
            ],
            "observed_at": []
          },
          "version": {
            "introduced_in": null,
            "valid_from": null,
            "valid_to": null,
            "verified_in": []
          },
          "evidence": [
            {
              "source_span_id": "span:be31670b07a2f290b7538287",
              "basis": "explicit_text",
              "authority": "non_authoritative"
            }
          ],
          "player_choice_scope": [
            "aether"
          ],
          "player_choice_branch": {
            "choice_id": "aether",
            "kind": "source_metadata",
            "source_span_id": "span:c50dd21b1b3186f074809cf6"
          },
          "player_choice_proof_refs": [
            "proof:fixture-span-c50dd21b1b3186f074809cf6"
          ]
        }
      ],
      "resolution": null
    }
  ],
  "test_refs": [],
  "attribute_records": [
    {
      "attribute_id": "attr:traveler-element",
      "key": "element",
      "value": "geo",
      "value_label": null,
      "identity_version_ids": [
        "identity:traveler"
      ],
      "story_scope": {
        "label": null,
        "from_event_id": null,
        "from_boundary": "unknown",
        "to_event_id": null,
        "to_boundary": "unknown"
      },
      "version": {
        "introduced_in": null,
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:7654af987c350552db259209",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [
        "conflict:element"
      ],
      "player_choice_scope": [],
      "player_choice_proof_refs": [
        "proof:fixture-span-b7138b253f2142467eae6a13"
      ],
      "observed_at": []
    },
    {
      "attribute_id": "attr:traveler-anemo",
      "key": "element",
      "value": "anemo",
      "value_label": null,
      "identity_version_ids": [
        "identity:traveler"
      ],
      "story_scope": {
        "label": null,
        "from_event_id": null,
        "from_boundary": "unknown",
        "to_event_id": null,
        "to_boundary": "unknown"
      },
      "version": {
        "introduced_in": null,
        "valid_from": null,
        "valid_to": null,
        "verified_in": []
      },
      "status": "candidate",
      "evidence": [
        {
          "source_span_id": "span:be31670b07a2f290b7538287",
          "basis": "explicit_text",
          "authority": "non_authoritative"
        }
      ],
      "conflict_ids": [
        "conflict:element"
      ],
      "player_choice_scope": [
        "aether"
      ],
      "player_choice_branch": {
        "choice_id": "aether",
        "kind": "source_metadata",
        "source_span_id": "span:c50dd21b1b3186f074809cf6"
      },
      "player_choice_proof_refs": [
        "proof:fixture-span-c50dd21b1b3186f074809cf6"
      ],
      "observed_at": []
    }
  ]
}
---
# 旅行者

> 候选页；来源内容尚未人工核验。

## 身份与归属
- 旅行者。[^source:https://example.invalid/traveler-conflict#p002]
- 名称：旅行者。[^source:https://example.invalid/traveler-conflict#p002]
- 名称：空（选择：空）。[^source:https://example.invalid/traveler-conflict#p004]
- 名称：荧（选择：荧）。[^source:https://example.invalid/traveler-conflict#p006]
- 组织状态：affiliated。[^source:https://example.invalid/traveler-conflict#p002]

## 时间线
- [版本 5.0] 共同旅行：旅行者是在提瓦特旅行的主角。[^source:https://example.invalid/traveler-conflict#p002]
- [版本 5.0] 空的风（选择：空）：空使用风元素。[^source:https://example.invalid/traveler-conflict#p004]
- [版本 5.0] 荧的岩（选择：荧）：荧使用岩元素。[^source:https://example.invalid/traveler-conflict#p006]

## 关系网络

## 争议与冲突
> [争议候选] 共同岩元素原值与空的风元素条件值并存（选择：空）
  - 共同原值在空分支下保持岩元素（选择：空）[^source:https://example.invalid/traveler-conflict#p002]
  - 空使用风元素（选择：空）[^source:https://example.invalid/traveler-conflict#p004]

[^source:https://example.invalid/traveler-conflict#p006]: [原文](https://example.invalid/traveler-conflict#p006) — span:613fa3bb6af7f4aa78b06a34
[^source:https://example.invalid/traveler-conflict#p002]: [原文](https://example.invalid/traveler-conflict#p002) — span:7654af987c350552db259209
[^source:https://example.invalid/traveler-conflict#p004]: [原文](https://example.invalid/traveler-conflict#p004) — span:be31670b07a2f290b7538287
