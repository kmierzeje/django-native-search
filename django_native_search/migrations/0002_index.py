# Generated by Django 3.0.8 on 2020-08-25 13:12

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('django_native_search', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Index',
            fields=[
            ],
            options={
                'verbose_name_plural': 'indexes',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('contenttypes.contenttype',),
        ),
    ]
