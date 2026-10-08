from django.db import migrations
TABLES=['meetinghub_event','meetinghub_agendarevision','meetinghub_roster','meetinghub_eligibility','meetinghub_readacknowledgment']
def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    if vendor=='postgresql':
        schema_editor.execute("CREATE OR REPLACE FUNCTION protect_meeting_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Meeting snapshot is immutable'; END; $$")
        for table in TABLES:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS phase2_immutable ON {table}')
            schema_editor.execute(f'CREATE TRIGGER phase2_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_meeting_event()')
    elif vendor=='sqlite':
        for table in TABLES:
            for action in ['update','delete']:
                name=table+'_phase2_'+action
                schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')
                schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {action.upper()} ON {table} BEGIN SELECT RAISE(ABORT, 'Meeting snapshot is immutable'); END")
def uninstall(apps,schema_editor):
    for table in TABLES:
        if schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'DROP TRIGGER IF EXISTS phase2_immutable ON {table}')
        elif schema_editor.connection.vendor=='sqlite':
            for action in ['update','delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_phase2_{action}')
class Migration(migrations.Migration):
    dependencies=[('meetinghub','0003_rsvpattempt_meetingrecord_agenda_version_and_more')]
    operations=[migrations.RunPython(install,uninstall)]
