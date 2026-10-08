from django.db import migrations

def protect(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        schema_editor.execute("CREATE TRIGGER mail_event_no_update BEFORE UPDATE ON correspondence_mailevent BEGIN SELECT RAISE(ABORT, 'Mail audit is immutable'); END")
        schema_editor.execute("CREATE TRIGGER mail_event_no_delete BEFORE DELETE ON correspondence_mailevent BEGIN SELECT RAISE(ABORT, 'Mail audit is immutable'); END")
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute("CREATE FUNCTION protect_mail_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Mail audit is immutable'; END; $$")
        schema_editor.execute("CREATE TRIGGER mail_event_immutable BEFORE UPDATE OR DELETE ON correspondence_mailevent FOR EACH ROW EXECUTE FUNCTION protect_mail_event()")

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        schema_editor.execute('DROP TRIGGER IF EXISTS mail_event_no_update'); schema_editor.execute('DROP TRIGGER IF EXISTS mail_event_no_delete')
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS mail_event_immutable ON correspondence_mailevent'); schema_editor.execute('DROP FUNCTION IF EXISTS protect_mail_event()')

class Migration(migrations.Migration):
    dependencies=[('correspondence','0001_initial')]
    operations=[migrations.RunPython(protect,unprotect)]
