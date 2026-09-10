# Переход на шифрование секрета TOTP (apps/iam/totp_crypto.py). Не
# RemoveField+AddField (как сгенерировал бы голый makemigrations по diff'у
# полей модели) — это стёрло бы уже сохранённые секреты существующих
# учёток при выкатке в прод. Вместо этого: RenameField сохраняет данные в
# totp_secret_plaintext (переходное поле, см. User.totp_secret и
# management-команду encrypt_totp_secrets), новое totp_secret_encrypted
# добавляется пустым и заполняется той же командой.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iam", "0006_remove_curator_add_methodist"),
    ]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="totp_secret",
            new_name="totp_secret_plaintext",
        ),
        migrations.AlterField(
            model_name="user",
            name="totp_secret_plaintext",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=64,
                verbose_name="Секрет TOTP (устар., открытый текст)",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="totp_secret_encrypted",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=255,
                verbose_name="Секрет TOTP (зашифрован)",
            ),
        ),
    ]
