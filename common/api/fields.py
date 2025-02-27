from rest_framework import serializers

from common.enums import FieldType


class CustomIntegerField(serializers.IntegerField):
    field_type = FieldType.INTEGER.value
    example_value = "610"

    def to_internal_value(self, data):
        if data == "":
            return None
        return super(CustomIntegerField, self).to_internal_value(data)


class CustomFloatField(serializers.FloatField):
    field_type = FieldType.FLOAT.value
    example_value = "61.981"


class CustomUUIDField(serializers.UUIDField):
    field_type = FieldType.UUID.value
    example_value = "12345678-1234-5678-1234-567812345678"


class CustomCharField(serializers.CharField):
    field_type = FieldType.STRING.value
    example_value = "Your message/string here."


class CustomEmailField(serializers.EmailField):
    field_type = FieldType.EMAIL.value
    example_value = "john@example.com"


class CustomBooleanField(serializers.BooleanField):
    field_type = FieldType.BOOLEAN.value
    example_value = "True"


class CustomJSONField(serializers.JSONField):
    field_type = FieldType.JSON.value
    example_value = """
    {
        "key_1": "value_1",
        "key_2": "value_2"
    }
    """


class CustomChoiceField(serializers.ChoiceField):
    field_type = FieldType.CHOICE.value
    example_value = "basic"


class CustomURLField(serializers.URLField):
    field_type = FieldType.URL.value
    example_value = "https://example.com"
