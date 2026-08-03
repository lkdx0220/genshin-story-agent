#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神剧情助手 RAG 记忆管理系统

基于 ChromaDB + Sentence-Transformers 实现跨会话语义检索。
采用延迟加载策略，仅在首次使用时导入 sentence-transformers。
"""

import os
import sys
import uuid
from typing import List, Dict, Any, Optional, Set
from datetime import datetime

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# Windows 兼容
if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


class GenshinConversationMemory:
    """原神剧情对话记忆存储（延迟加载依赖）"""

    # 类级别：避免重复加载和初始化
    _sentence_transformer = None
    _chroma_client = None
    _collections: Dict[str, Any] = {}
    _st_checked: bool = False
    _st_available: bool = False
    _chroma_checked: bool = False
    _chroma_available: bool = False

    def __init__(self, persist_directory: str = "./conversation_memory",
                 embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 collection_name: str = "genshin_conversations"):
        self.persist_dir = persist_directory
        self.embedding_model_name = embedding_model
        self.collection_name = collection_name
        self._collection = None
        self._initialized = False

    def _ensure_init(self):
        """延迟初始化：首次调用时才加载依赖"""
        if self._initialized:
            return

        # 检测 sentence-transformers
        if not self._st_checked:
            try:
                from sentence_transformers import SentenceTransformer
                self.__class__._sentence_transformer = SentenceTransformer
                self.__class__._st_available = True
                self.__class__._st_checked = True
                print("[记忆] Sentence-Transformers 就绪")
            except ImportError:
                self.__class__._st_available = False
                self.__class__._st_checked = True
                print("[记忆] Sentence-Transformers 未安装，记忆功能不可用")
            except Exception as e:
                self.__class__._st_available = False
                self.__class__._st_checked = True
                print(f"[记忆] Sentence-Transformers 初始化失败: {e}")

        # 检测 chromadb
        if not self._chroma_checked:
            try:
                import chromadb
                self.__class__._chroma_available = True
                self.__class__._chroma_checked = True
                print("[记忆] ChromaDB 就绪")
            except ImportError:
                self.__class__._chroma_available = False
                self.__class__._chroma_checked = True
                print("[记忆] ChromaDB 未安装，记忆功能不可用")
            except Exception as e:
                self.__class__._chroma_available = False
                self.__class__._chroma_checked = True
                print(f"[记忆] ChromaDB 初始化失败: {e}")

        # 如果两者都不可用，初始化完成但不工作
        if not self._st_available or not self._chroma_available:
            self._initialized = True
            return

        # 初始化 ChromaDB 集合
        try:
            key = f"{self.persist_dir}/{self.collection_name}"
            if key in self._collections:
                self._collection = self._collections[key]
            else:
                os.makedirs(self.persist_dir, exist_ok=True)
                db_file = os.path.join(self.persist_dir, "chroma.sqlite3")
                if os.path.exists(db_file):
                    try:
                        import stat
                        os.chmod(db_file, stat.S_IWRITE | stat.S_IREAD)
                    except Exception:
                        pass

                if not self._chroma_client:
                    import chromadb
                    self.__class__._chroma_client = chromadb.PersistentClient(path=self.persist_dir)

                # 检查或创建集合
                try:
                    existing = self._chroma_client.get_collection(self.collection_name)
                    space = existing.metadata.get("hnsw:space", "l2") if existing.metadata else "l2"
                    if space != "cosine":
                        self._chroma_client.delete_collection(self.collection_name)
                        col = self._chroma_client.create_collection(
                            name=self.collection_name,
                            metadata={"description": "原神剧情对话记忆", "hnsw:space": "cosine"}
                        )
                    else:
                        col = existing
                except Exception:
                    col = self._chroma_client.create_collection(
                        name=self.collection_name,
                        metadata={"description": "原神剧情对话记忆", "hnsw:space": "cosine"}
                    )

                self._collection = col
                self._collections[key] = col
                print(f"[记忆] 集合 '{self.collection_name}' 就绪 ({col.count()}条)")

        except Exception as e:
            print(f"[记忆] ChromaDB 集合初始化失败: {e}")
            self._chroma_available = False

        self._initialized = True

    @property
    def is_available(self) -> bool:
        self._ensure_init()
        return self._st_available and self._chroma_available and self._collection is not None

    def add_conversation(self, user_query: str, assistant_response: str,
                         metadata: Optional[Dict] = None) -> Optional[str]:
        self._ensure_init()
        if not self.is_available:
            return None

        memory_id = str(uuid.uuid4())
        text = f"用户: {user_query}\n\n助手: {assistant_response}"

        meta = metadata or {}
        meta.update({
            "timestamp": datetime.now().isoformat(),
            "user_preview": user_query[:100],
        })

        try:
            ST = self._sentence_transformer
            model = ST(self.embedding_model_name)
            embedding = model.encode(text).tolist()
            self._collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[meta],
            )
            return memory_id
        except Exception as e:
            print(f"[记忆] 保存失败: {e}")
            return None

    def retrieve(self, query: str, top_k: int = 3, threshold: float = 0.4) -> List[Dict]:
        self._ensure_init()
        if not self.is_available:
            return []

        try:
            ST = self._sentence_transformer
            model = ST(self.embedding_model_name)
            query_emb = model.encode(query).tolist()
            results = self._collection.query(
                query_embeddings=[query_emb],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )

            memories = []
            if results and results.get("documents"):
                for i, doc in enumerate(results["documents"][0]):
                    distance = results["distances"][0][i] if results.get("distances") else 1.0
                    similarity = 1 - distance
                    if similarity >= threshold:
                        memories.append({
                            "content": doc,
                            "similarity": similarity,
                            "timestamp": results["metadatas"][0][i].get("timestamp", ""),
                        })
            return memories
        except Exception as e:
            print(f"[记忆] 检索失败: {e}")
            return []

    def build_context(self, memories: List[Dict]) -> str:
        if not memories:
            return ""

        ctx = "\n【历史对话回忆】\n" + "-" * 40 + "\n"
        for i, m in enumerate(memories, 1):
            ts = m.get("timestamp", "")
            if ts:
                try:
                    ts = datetime.fromisoformat(ts).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            ctx += f"\n[{i}] {ts}\n{m['content']}\n"
        ctx += "-" * 40 + "\n"
        return ctx

    def get_stats(self) -> Dict:
        self._ensure_init()
        if not self._collection:
            return {"status": "unavailable", "count": 0}
        try:
            return {"status": "available", "count": self._collection.count()}
        except Exception:
            return {"status": "error", "count": 0}


class GenshinRAGMemory:
    """原神剧情助手 RAG 记忆模块（对外接口）"""

    def __init__(self, persist_dir: str = "./conversation_memory"):
        self.memory = GenshinConversationMemory(
            persist_directory=persist_dir,
            embedding_model="paraphrase-multilingual-MiniLM-L12-v2",
            collection_name="genshin_conversations",
        )

    def save_conversation(self, user_query: str, assistant_response: str,
                          session_id: str = "default") -> None:
        self.memory.add_conversation(user_query, assistant_response,
                                     metadata={"session_id": session_id})

    def retrieve_for_query(self, query: str, top_k: int = 3) -> str:
        memories = self.memory.retrieve(query, top_k=top_k, threshold=0.4)
        return self.memory.build_context(memories)

    def get_stats(self) -> Dict:
        return self.memory.get_stats()
