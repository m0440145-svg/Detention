from django.db import migrations

def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute("CREATE FUNCTION detention_audit_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Audit records are immutable'; END; $$ LANGUAGE plpgsql;")
        schema_editor.execute('CREATE TRIGGER detention_audit_immutable BEFORE UPDATE OR DELETE ON core_audit FOR EACH ROW EXECUTE FUNCTION detention_audit_immutable();')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute("CREATE TRIGGER detention_audit_update BEFORE UPDATE ON core_audit BEGIN SELECT RAISE(ABORT, 'Audit records are immutable'); END;")
        schema_editor.execute("CREATE TRIGGER detention_audit_delete BEFORE DELETE ON core_audit BEGIN SELECT RAISE(ABORT, 'Audit records are immutable'); END;")
def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS detention_audit_immutable ON core_audit;')
        schema_editor.execute('DROP FUNCTION IF EXISTS detention_audit_immutable();')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute('DROP TRIGGER IF EXISTS detention_audit_update;'); schema_editor.execute('DROP TRIGGER IF EXISTS detention_audit_delete;')
class Migration(migrations.Migration):
    dependencies=[('core','0001_initial')]
    operations=[migrations.RunPython(install,uninstall)]
