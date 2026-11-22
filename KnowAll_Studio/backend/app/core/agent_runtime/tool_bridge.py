"""Allowlisted document tools with server-bound identity and bounded SQL reads."""
import asyncio
import logging
import hashlib
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator
from sqlalchemy import select,or_
from fastapi import HTTPException
from app.models import Document,DocumentChunk
from app.core.documents import assert_document_access
from .contracts import ToolResult

logger=logging.getLogger('knowall.agent.tools')


class SearchArguments(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    query: str=Field(min_length=1,max_length=200)
    limit: int=Field(default=5,ge=1,le=5)

    @field_validator('query')
    @classmethod
    def nonempty(cls,value):
        if not value.strip(): raise ValueError('query cannot be blank')
        return value.strip()


class ReadArguments(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    chunk_id: str=Field(min_length=1,max_length=36)


class DocumentToolBridge:
    """A per-run bridge. Shared legacy documents retain existing access rules."""
    schemas={'search_documents':SearchArguments,'read_document_chunk':ReadArguments}
    descriptions={
        'search_documents':'按原文关键词搜索可访问的学习资料，返回最多5个片段和来源；是关键词检索。',
        'read_document_chunk':'读取可访问资料的指定片段，返回最多4000字原文及页码；片段ID来自搜索结果。',
    }

    def __init__(self,session_factory,user_id:str,*,enabled:bool=True,timeout:float=5):
        self.session_factory=session_factory
        self.user_id=user_id
        self.enabled=enabled
        self.timeout=timeout
        self.sources: dict[str,dict]={}

    def definitions(self):
        if not self.enabled: return []
        return [{'type':'function','function':{'name':name,'description':self.descriptions[name],
                    'parameters':schema.model_json_schema()}} for name,schema in self.schemas.items()]

    def _source(self,doc,chunk,limit):
        source={'citation_id': self.sources.get(chunk.id,{}).get('citation_id',f'S{len(self.sources)+1}'),
                'document_id':doc.id,'chunk_id':chunk.id,'filename':doc.filename,
                'page_range':chunk.page_range,'text':chunk.text_content[:limit],
                'chunk_sha256':hashlib.sha256(chunk.text_content.encode('utf8')).hexdigest(),
                'truncated':len(chunk.text_content)>limit}
        self.sources[chunk.id]=source
        return source

    async def verified_sources(self,citation_ids:list[str]):
        """Re-read cited records after model awaits; changed/deleted sources fail."""
        by_citation={source['citation_id']:source for source in self.sources.values()}
        if not citation_ids or any(key not in by_citation for key in citation_ids):
            return None
        async with asyncio.timeout(self.timeout):
            async with self.session_factory() as db:
                for key in citation_ids:
                    source=by_citation[key]
                    chunk=await db.get(DocumentChunk,source['chunk_id'])
                    if chunk is None or chunk.doc_id!=source['document_id']:
                        return None
                    try: doc=await assert_document_access(db,chunk.doc_id,self.user_id)
                    except HTTPException: return None
                    if (doc is None or doc.filename!=source['filename'] or chunk.page_range!=source['page_range']
                            or hashlib.sha256(chunk.text_content.encode('utf8')).hexdigest()!=source['chunk_sha256']):
                        return None
        return [by_citation[key] for key in citation_ids]

    async def execute(self,call_id,name,arguments):
        if not self.enabled or name not in self.schemas:
            return ToolResult(call_id,'error',{'error':'UNKNOWN_TOOL'})
        try: args=self.schemas[name].model_validate(arguments)
        except ValidationError:
            return ToolResult(call_id,'error',{'error':'INVALID_ARGUMENTS'})
        try:
            async with asyncio.timeout(self.timeout):
                async with self.session_factory() as db:
                    if name=='search_documents':
                        owner=Document.metadata_['knowledge_owner_id'].as_string()
                        rows=(await db.execute(select(Document,DocumentChunk)
                            .join(DocumentChunk,DocumentChunk.doc_id==Document.id)
                            .where(or_(owner.is_(None),owner=='',owner==self.user_id))
                            .where(or_(DocumentChunk.text_content.contains(args.query,autoescape=True),
                                       Document.filename.contains(args.query,autoescape=True)))
                            .order_by(Document.created_at.desc(),Document.id,DocumentChunk.chunk_index)
                            .limit(args.limit))).all()
                        sources=[self._source(doc,chunk,1000) for doc,chunk in rows]
                    else:
                        chunk=await db.get(DocumentChunk,args.chunk_id)
                        if chunk is None:
                            return ToolResult(call_id,'error',{'error':'SOURCE_NOT_FOUND'})
                        doc=await assert_document_access(db,chunk.doc_id,self.user_id)
                        if doc is None:
                            return ToolResult(call_id,'error',{'error':'SOURCE_NOT_FOUND'})
                        sources=[self._source(doc,chunk,4000)]
                    return ToolResult(call_id,'success',{'sources':sources})
        except HTTPException:
            return ToolResult(call_id,'error',{'error':'SOURCE_NOT_FOUND'})
        except TimeoutError:
            return ToolResult(call_id,'error',{'error':'TOOL_TIMEOUT'})
        except Exception:
            logger.exception('Read-only document tool failed')
            return ToolResult(call_id,'error',{'error':'TOOL_UNAVAILABLE'})
