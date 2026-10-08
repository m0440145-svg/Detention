from django.db import migrations

def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute("CREATE FUNCTION protect_meeting_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Meeting audit is immutable'; END; $$")
        schema_editor.execute('CREATE TRIGGER meeting_event_immutable BEFORE UPDATE OR DELETE ON meetinghub_event FOR EACH ROW EXECUTE FUNCTION protect_meeting_event()')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute("CREATE TRIGGER meeting_event_no_update BEFORE UPDATE ON meetinghub_event BEGIN SELECT RAISE(ABORT, 'Meeting audit is immutable'); END")
        schema_editor.execute("CREATE TRIGGER meeting_event_no_delete BEFORE DELETE ON meetinghub_event BEGIN SELECT RAISE(ABORT, 'Meeting audit is immutable'); END")
def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS meeting_event_immutable ON meetinghub_event');schema_editor.execute('DROP FUNCTION IF EXISTS protect_meeting_event()')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute('DROP TRIGGER IF EXISTS meeting_event_no_update');schema_editor.execute('DROP TRIGGER IF EXISTS meeting_event_no_delete')
class Migration(migrations.Migration):
    dependencies=[('meetinghub','0001_initial')]
    operations=[migrations.RunPython(install,uninstall)]
