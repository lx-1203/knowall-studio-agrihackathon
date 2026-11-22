"""Observe runtime storage readiness without changing schema or calling a model."""
from sqlalchemy import inspect

_COLUMNS = {
    'agent_runs': {'id','user_id','conversation_id','assistant_message_id','status','scope','model','sources','steps','error_code','created_at','completed_at'},
    'agent_background_tasks': {'run_id','user_id','cancel_requested','created_at'},
    'agent_input_checkpoints': {'run_id','user_id','input_ciphertext','version','created_at'},
}

def _storage(connection):
    schema=inspect(connection)
    tables=set(schema.get_table_names())
    return {name:name in tables and columns.issubset({c['name'] for c in schema.get_columns(name)})
            for name,columns in _COLUMNS.items()}

async def runtime_capabilities(db, *, accepting_background=True):
    # No cache: an incomplete or changed schema must never enable a client
    # action based on a previous process/database's readiness observation.
    connection=await db.connection()
    ready=await connection.run_sync(_storage)
    records=ready['agent_runs']
    background=records and ready['agent_background_tasks']
    return {'durable_runs':records,'background_runs':background and accepting_background,
            'input_checkpoints':background and ready['agent_input_checkpoints'],
            'streaming_events':records,'streaming_tools':False,'execution_resume':False}
