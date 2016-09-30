from __future__ import unicode_literals, absolute_import
import re
from datetime import date, time
from datetime import datetime

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.db import models
from django.db.models import QuerySet
from bs4 import BeautifulSoup
import pytest
from django.test import RequestFactory
from django.utils.encoding import smart_text

from tests.models import Foo, FieldFromModelOneToOneTest, FormFromModelTest, FooField, RegisterFieldFactoryTest, FieldFromModelForeignKeyTest, FieldFromModelManyToManyTest, Bar
from tri.declarative import getattr_path, setattr_path
from tri.struct import Struct
from tri.form import BoundField, AVOID_EMPTY_FORM, Form, Field, register_field_factory


def assert_one_error_and_matches_reg_exp(errors, reg_exp):
    error = list(errors)[0]
    assert len(errors) == 1
    assert re.search(reg_exp, error)


def test_declaration_merge():

    class MyForm(Form):
        class Meta:
            fields = [Field(name='foo')]

        bar = Field()

    assert {'foo', 'bar'} == set(MyForm().fields_by_name.keys())


class MyTestForm(Form):
    party = Field.choice(choices=['ABC'], required=False)
    username = Field(
        is_valid=lambda form, field, parsed_data: (
            parsed_data.startswith(form.fields_by_name['party'].parsed_data.lower() + '_') if parsed_data is not None else None,
            'Username must begin with "%s_"' % form.fields_by_name['party'].parsed_data)
    )
    joined = Field.datetime(attr='contact__joined')
    a_date = Field.date()
    a_time = Field.time()
    staff = Field.boolean()
    admin = Field.boolean()
    manages = Field.multi_choice(choices=['DEF', 'KTH', 'LIU'], required=False)
    not_editable = Field.text(initial='Some non-editable text', editable=False)

    # TODO: tests for all shortcuts with required=False


def test_required():
    form = MyTestForm(request=RequestFactory().post('/', {'-': '-'}))
    assert form.fields_by_name['a_date'].value is None
    assert form.fields_by_name['a_date'].errors == {'This field is required'}


def test_required_with_falsy_option():
    class MyForm(Form):
        foo = Field.choice(
            choices=[0, 1],
            parse=lambda string_value, **_: int(string_value)
        )
    form = MyForm(request=RequestFactory().post('/', {'foo': '0', '-': '-'}))
    assert form.fields_by_name.foo.value == 0
    assert form.fields_by_name.foo.errors == set()


def test_parse():
    # The spaces in the data are there to check that we strip input
    form = MyTestForm(
        request=RequestFactory().post('/', {
            'party': 'ABC ',
            'username': 'abc_foo ',
            'joined': ' 2014-12-12 01:02:03  ',
            'staff': ' true',
            'admin': 'false ',
            'manages': ['DEF  ', 'KTH '],
            'a_date': '  2014-02-12  ',
            'a_time': '  01:02:03  ',
            '-': '-',
        }))

    assert [x.errors for x in form.fields] == [set() for _ in form.fields]
    assert form.is_valid()
    assert form.fields_by_name['party'].parsed_data == 'ABC'
    assert form.fields_by_name['party'].value == 'ABC'

    assert form.fields_by_name['username'].parsed_data == 'abc_foo'
    assert form.fields_by_name['username'].value == 'abc_foo'

    assert form.fields_by_name['joined'].raw_data == '2014-12-12 01:02:03'
    assert form.fields_by_name['joined'].parsed_data == datetime(2014, 12, 12, 1, 2, 3)
    assert form.fields_by_name['joined'].value == datetime(2014, 12, 12, 1, 2, 3)

    assert form.fields_by_name['staff'].raw_data == 'true'
    assert form.fields_by_name['staff'].parsed_data is True
    assert form.fields_by_name['staff'].value is True

    assert form.fields_by_name['admin'].raw_data == 'false'
    assert form.fields_by_name['admin'].parsed_data is False
    assert form.fields_by_name['admin'].value is False

    assert form.fields_by_name['manages'].raw_data_list == ['DEF', 'KTH']
    assert form.fields_by_name['manages'].parsed_data_list == ['DEF', 'KTH']
    assert form.fields_by_name['manages'].value_list == ['DEF', 'KTH']

    assert form.fields_by_name['a_date'].raw_data == '2014-02-12'
    assert form.fields_by_name['a_date'].parsed_data == date(2014, 2, 12)
    assert form.fields_by_name['a_date'].value == date(2014, 2, 12)

    assert form.fields_by_name['a_time'].raw_data == '01:02:03'
    assert form.fields_by_name['a_time'].parsed_data == time(1, 2, 3)
    assert form.fields_by_name['a_time'].value == time(1, 2, 3)

    instance = Struct(contact=Struct())
    form.apply(instance)
    assert instance == Struct(
        contact=Struct(joined=datetime(2014, 12, 12, 1, 2, 3)),
        party='ABC',
        staff=True,
        admin=False,
        username='abc_foo',
        manages=['DEF', 'KTH'],
        a_date=date(2014, 2, 12),
        a_time=time(1, 2, 3),
        not_editable='Some non-editable text')


def test_parse_errors():
    def post_validation(form):
        form.add_error('General snafu')
    form = MyTestForm(
        data=dict(
            party='foo',
            username='bar_foo',
            joined='foo',
            staff='foo',
            admin='foo',
            a_date='fooasd',
            a_time='asdasd',
            **{'-': ''}
        ),
        post_validation=post_validation)

    assert not form.is_valid()

    assert form.errors == {'General snafu'}

    assert form.fields_by_name['party'].parsed_data == 'foo'
    assert form.fields_by_name['party'].errors == {'foo not in available choices'}
    assert form.fields_by_name['party'].value is None

    assert form.fields_by_name['username'].parsed_data == 'bar_foo'
    assert form.fields_by_name['username'].errors == {'Username must begin with "foo_"'}
    assert form.fields_by_name['username'].value is None

    assert form.fields_by_name['joined'].raw_data == 'foo'
    assert_one_error_and_matches_reg_exp(form.fields_by_name['joined'].errors, 'Time data "foo" does not match any of the formats .*')
    assert form.fields_by_name['joined'].parsed_data is None
    assert form.fields_by_name['joined'].value is None

    assert form.fields_by_name['staff'].raw_data == 'foo'
    assert form.fields_by_name['staff'].parsed_data is None
    assert form.fields_by_name['staff'].value is None

    assert form.fields_by_name['admin'].raw_data == 'foo'
    assert form.fields_by_name['admin'].parsed_data is None
    assert form.fields_by_name['admin'].value is None

    assert form.fields_by_name['a_date'].raw_data == 'fooasd'
    assert_one_error_and_matches_reg_exp(form.fields_by_name['a_date'].errors, "time data u?'fooasd' does not match format u?'%Y-%m-%d'")
    assert form.fields_by_name['a_date'].parsed_data is None
    assert form.fields_by_name['a_date'].value is None

    assert form.fields_by_name['a_time'].raw_data == 'asdasd'
    assert_one_error_and_matches_reg_exp(form.fields_by_name['a_time'].errors, "time data u?'asdasd' does not match format u?'%H:%M:%S'")
    assert form.fields_by_name['a_time'].parsed_data is None
    assert form.fields_by_name['a_time'].value is None

    with pytest.raises(AssertionError):
        form.apply(Struct())


def test_initial_from_instance():
    assert Form(instance=Struct(a=Struct(b=7)), fields=[Field(name='a__b')]).fields[0].initial == 7


def test_initial_list_from_instance():
    assert Form(instance=Struct(a=Struct(b=[7])), fields=[Field(name='a__b', is_list=True)]).fields[0].initial_list == [7]


def test_non_editable_from_initial():
    class MyForm(Form):
        foo = Field(editable=False, initial=':bar:')

    assert ':bar:' in MyForm(request=RequestFactory().get('/')).render()
    assert ':bar:' in MyForm(request=RequestFactory().post('/', {'-': '-'})).render()


def test_show():
    assert list(Form(data={}, fields=[Field(name='foo', show=False)]).fields_by_name.keys()) == []
    assert list(Form(data={}, fields=[Field(name='foo', show=lambda form, field: False)]).fields_by_name.keys()) == []


def test_non_editable():
    assert Form(data={}, fields=[Field(name='foo', editable=False)]).fields[0].input_template == 'tri_form/non_editable.html'


def test_text_fields():
    assert '<input type="text" ' in Form(data={}, fields=[Field.text(name='foo')]).compact()
    assert '<textarea' in Form(data={}, fields=[Field.textarea(name='foo')]).compact()


def test_integer_field():
    assert Form(data=dict(foo=' 7  '), fields=[Field.integer(name='foo')]).fields[0].parsed_data == 7
    actual_errors = Form(data=dict(foo=' foo  '), fields=[Field.integer(name='foo')]).fields[0].errors
    assert_one_error_and_matches_reg_exp(actual_errors, "invalid literal for int\(\) with base 10: u?'foo'")


def test_float_field():
    assert Form(data=dict(foo=' 7.3  '), fields=[Field.float(name='foo')]).fields[0].parsed_data == 7.3
    assert Form(data=dict(foo=' foo  '), fields=[Field.float(name='foo')]).fields[0].errors == {"could not convert string to float: foo"}


def test_email_field():
    assert Form(data=dict(foo=' 5  '), fields=[Field.email(name='foo')]).fields[0].errors == {u'Enter a valid email address.'}
    assert Form(data=dict(foo='foo@example.com'), fields=[Field.email(name='foo')]).is_valid()


def test_multi_email():
    assert Form(data=dict(foo='foo@example.com, foo@example.com'), fields=[Field.comma_separated(Field.email(name='foo'))]).is_valid()


def test_comma_separated_errors_on_parse():
    def raise_always_value_error(string_value, **_):
        raise ValueError('foo %s!' % string_value)

    def raise_always_validation_error(string_value, **_):
        raise ValidationError(['foo %s!' % string_value, 'bar %s!' % string_value])

    assert Form(
        data=dict(foo='5, 7'),
        fields=[Field.comma_separated(Field(name='foo', parse=raise_always_value_error))]).fields[0].errors == {
            u'Invalid value "5": foo 5!',
            u'Invalid value "7": foo 7!',
    }

    assert Form(
        data=dict(foo='5, 7'),
        fields=[Field.comma_separated(Field(name='foo', parse=raise_always_validation_error))]).fields[0].errors == {
            u'Invalid value "5": foo 5!',
            u'Invalid value "5": bar 5!',
            u'Invalid value "7": foo 7!',
            u'Invalid value "7": bar 7!',
    }


def test_comma_separated_errors_on_validation():
    assert Form(
        data=dict(foo='5, 7'),
        fields=[Field.comma_separated(Field(name='foo', is_valid=lambda parsed_data, **_: (False, 'foo %s!' % parsed_data)))]).fields[0].errors == {
            u'Invalid value "5": foo 5!',
            u'Invalid value "7": foo 7!',
    }


def test_phone_field():
    assert Form(data=dict(foo=' asdasd  '), fields=[Field.phone_number(name='foo')]).fields[0].errors == {u'Please use format +<country code> (XX) XX XX. Example of US number: +1 (212) 123 4567 or +1 212 123 4567'}
    assert Form(data=dict(foo='+1 (212) 123 4567'), fields=[Field.phone_number(name='foo')]).is_valid()
    assert Form(data=dict(foo='+46 70 123 123'), fields=[Field.phone_number(name='foo')]).is_valid()


def test_render_template_string():
    assert Form(data=dict(foo='7'), fields=[Field(name='foo', template=None, template_string='{{ field.value }} {{ form.style }}')]).compact() == '7 compact\n' + AVOID_EMPTY_FORM


def test_render_template():
    assert '<form' in Form(request=RequestFactory().get('/'), data=dict(foo='7'), fields=[Field(name='foo')]).render()


def test_render_attrs():
    assert Form(data=dict(foo='7'), fields=[Field(name='foo', attrs={'foo': '1'})]).fields[0].render_attrs() == ' foo="1"'
    assert Form(data=dict(foo='7'), fields=[Field(name='foo')]).fields[0].render_attrs() == ' '


def test_render_attrs_new_style():
    assert Form(data=dict(foo='7'), fields=[Field(name='foo', attrs__foo='1')]).fields[0].render_attrs() == ' foo="1"'
    assert Form(data=dict(foo='7'), fields=[Field(name='foo')]).fields[0].render_attrs() == ' '


def test_bound_field_render_css_classes():
    assert BoundField(
        field=Struct(
            container_css_classes={'a', 'b'},
            required=True,
        ),
        form=Struct(style='compact')).render_container_css_classes() == ' class="a b key-value required"'


def test_getattr_path():
    assert getattr_path(Struct(a=1), 'a') == 1
    assert getattr_path(Struct(a=Struct(b=2)), 'a__b') == 2
    with pytest.raises(AttributeError):
        getattr_path(Struct(a=2), 'b')

    assert getattr_path(Struct(a=None), 'a__b__c__d') is None


def test_setattr_path():
    assert getattr_path(setattr_path(Struct(a=0), 'a', 1), 'a') == 1
    assert getattr_path(setattr_path(Struct(a=Struct(b=0)), 'a__b', 2), 'a__b') == 2

    with pytest.raises(AttributeError):
        setattr_path(Struct(a=1), 'a__b', 1)


def test_multi_select_with_one_value_only():
    assert ['a'] == Form(data=dict(foo=['a']), fields=[Field.multi_choice(name='foo', choices=['a', 'b'])]).fields[0].value_list


def test_render_table():
    class MyForm(Form):
        class Meta:
            data = dict(foo='!!!7!!!')

        foo = Field(
            input_container_css_classes={'###5###'},
            label_container_css_classes={'$$$11$$$'},
            help_text='^^^13^^^',
            label='***17***',
            id='$$$$5$$$$$'
        )

    table = MyForm().table()
    assert '!!!7!!!' in table
    assert '###5###' in table
    assert '$$$11$$$' in table
    assert '^^^13^^^' in table
    assert '***17***' in table
    assert 'id="$$$$5$$$$$"' in table
    assert '<tr' in table

    # Assert that table is the default
    assert table == "%s" % MyForm()


def test_heading():
    assert '<th colspan="2">#foo#</th>' in Form(data={}, fields=[Field.heading(label='#foo#')]).table()


def test_info():
    form = Form(data={}, fields=[Field.info(value='#foo#')])
    assert form.is_valid()
    assert '#foo#' in form.table()


def test_radio():
    choices = [
        'a',
        'b',
        'c',
    ]
    soup = BeautifulSoup(Form(data=dict(foo='a'), fields=[Field.radio(name='foo', choices=choices)]).table())
    assert len(soup.find_all('input')) == len(choices) + 1  # +1 for AVOID_EMPTY_FORM
    assert [x.attrs['value'] for x in soup.find_all('input') if 'checked' in x.attrs] == ['a']


def test_hidden():
    soup = BeautifulSoup(Form(data=dict(foo='1'), fields=[Field.hidden(name='foo')]).table())
    assert [(x.attrs['type'], x.attrs['value']) for x in soup.find_all('input')] == [('hidden', '1'), ('hidden', '-')]


def test_password():
    assert ' type="password" ' in Form(data=dict(foo='1'), fields=[Field.password(name='foo')]).table()


def test_choice_not_required():
    class MyForm(Form):
        foo = Field.choice(required=False, choices=['bar'])

    assert MyForm(request=RequestFactory().post('/', {'foo': 'bar', '-': '-'})).fields[0].value == 'bar'
    assert MyForm(request=RequestFactory().post('/', {'foo': '', '-': '-'})).fields[0].value is None


def test_multi_choice():
    soup = BeautifulSoup(Form(data=dict(foo=['0']), fields=[Field.multi_choice(name='foo', choices=['a'])]).table())
    assert [x.attrs['multiple'] for x in soup.find_all('select')] == ['']


def test_help_text_from_model():
    assert Form(data=dict(foo='1'), fields=[Field.from_model(model=Foo, field_name='foo')], model=Foo).fields[0].help_text == 'foo_help_text'


@pytest.mark.django_db
def test_choice_query():
    user = User.objects.create(username='foo')
    user2 = User.objects.create(username='foo2')

    class MyForm(Form):
        foo = Field.multi_choice_queryset(attr=None, model=User, choices=User.objects.exclude(username=user2.username))

    assert [x.pk for x in MyForm().fields[0].choices] == [user.pk]
    assert MyForm(RequestFactory().get('/', {'foo': smart_text(user2.pk)})).fields[0].errors == {'%s not in available choices' % user2.pk}


def test_field_from_model():
    class FooForm(Form):
        foo = Field.from_model(Foo, 'foo')

        class Meta:
            model = Foo

    assert FooForm(data=dict(foo='1')).fields[0].value == 1
    assert not FooForm(data=dict(foo='asd')).is_valid()


def test_form_from_model_valid_form():
    assert [x.value for x in Form.from_model(
        model=FormFromModelTest,
        include=['f_int', 'f_float', 'f_bool'],
        data=dict(f_int='1', f_float='1.1', f_bool='true')
    ).fields] == [
        1,
        1.1,
        True
    ]


def test_form_from_model_invalid_form():
    actual_errors = [x.errors for x in Form.from_model(
        model=FormFromModelTest,
        exclude=['f_int_excluded'],
        data=dict(f_int='1.1', f_float='true', f_bool='asd', f_file='foo')
    ).fields]

    assert len(actual_errors) == 4
    assert {'could not convert string to float: true'} in actual_errors
    assert {u'asd is not a valid boolean value'} in actual_errors
    assert {"invalid literal for int() with base 10: '1.1'"} in actual_errors or {"invalid literal for int() with base 10: u'1.1'"} in actual_errors


def test_field_from_model_supports_all_types():
    from django.db.models import fields
    not_supported = []
    blacklist = {
        'AutoField',
        'Field',
        'BigAutoField',
        'BinaryField',
        'FilePathField',
        'GenericIPAddressField',
        'IPAddressField',
        'NullBooleanField',
        'SlugField',
        'DurationField',
        'UUIDField'
    }
    field_type_names = [x for x in dir(fields) if x.endswith('Field') and x not in blacklist]

    for name in field_type_names:
        field_type = getattr(fields, name)
        try:
            Field.from_model(model=Foo, model_field=field_type())
        except AssertionError:  # pragma: no cover
            not_supported.append(name)

    assert not_supported == []


def test_field_from_model_blank_handling():
    from django.db.models import CharField

    subject = Field.from_model(model=Foo, model_field=CharField(blank=False))
    assert True is subject.parse_empty_string_as_none

    subject = Field.from_model(model=Foo, model_field=CharField(blank=True))
    assert False is subject.parse_empty_string_as_none


@pytest.mark.django_db
def test_field_from_model_foreign_key():
    Foo.objects.create(foo=2)
    Foo.objects.create(foo=3)
    Foo.objects.create(foo=5)
    choices = Field.from_model(FieldFromModelForeignKeyTest, 'foo_fk').choices
    assert isinstance(choices, QuerySet)
    assert set(choices) == set(Foo.objects.all())


@pytest.mark.django_db
def test_field_from_model_many_to_many():
    Foo.objects.create(foo=2)
    Foo.objects.create(foo=3)
    Foo.objects.create(foo=5)
    choices = Field.from_model(FieldFromModelManyToManyTest, 'foo_many_to_many').choices
    assert isinstance(choices, QuerySet)
    assert set(choices) == set(Foo.objects.all())


@pytest.mark.django_db
def test_field_from_model_foreign_key2():
    form = Form.from_model(
        data={},
        model=FieldFromModelOneToOneTest,
        field__foo_one_to_one__class=Field.from_model_expand,
        field__foo_one_to_one__field__foo__label='blaha',
        field__foo_one_to_one__field__foo__extra__stuff='blahada',
    )
    assert set(form.fields_by_name.keys()) == {'foo_one_to_one__foo'}
    assert form.fields_by_name['foo_one_to_one__foo'].label == 'blaha'
    assert form.fields_by_name['foo_one_to_one__foo'].extra.stuff == 'blahada'


@pytest.mark.django_db
def test_field_from_model_many_to_one_foreign_key():
    assert set(Form.from_model(
        data={},
        model=Bar,
        field__foo__class=Field.from_model
    ).fields_by_name.keys()) == {'foo'}


def test_register_field_factory():
    register_field_factory(FooField, lambda **kwargs: 7)

    assert Field.from_model(RegisterFieldFactoryTest, 'foo') == 7


def shortcut_test(shortcut, raw_and_parsed_data_tuples, normalizing=None):
    SENTINEL = object()
    if normalizing is None:
        normalizing = []

    def test_empty_string_data():
        f = Form(fields=[shortcut(required=False, name='foo')], data={'foo': ''})
        assert not f.get_errors()
        assert f.fields_by_name['foo'].value is None
        assert f.fields_by_name['foo'].value_list is None

    def test_empty_data():
        f = Form(fields=[shortcut(required=False, name='foo')], data={})
        assert not f.get_errors()
        assert f.fields_by_name['foo'].value is None
        assert f.fields_by_name['foo'].value_list is None

    def test_editable_false():
        f = Form(fields=[shortcut(required=False, name='foo', initial=SENTINEL, editable=False)], data={'foo': 'asdasasd'})
        assert not f.get_errors()
        assert f.fields_by_name['foo'].value is SENTINEL or f.fields_by_name['foo'].value_list is SENTINEL

    def test_roundtrip_from_initial_to_raw_string():
        for raw, initial in raw_and_parsed_data_tuples:
            form = Form(fields=[shortcut(required=True, name='foo', initial=initial)], data={})
            assert not form.get_errors()
            f = form.fields_by_name['foo']
            if f.is_list:
                assert initial == f.value_list
            else:
                assert initial == f.value
            assert raw == f.rendered_value(), 'Roundtrip failed'

    def test_roundtrip_from_raw_string_to_initial():
        for raw, initial in raw_and_parsed_data_tuples:
            form = Form(fields=[shortcut(required=True, name='foo')], data={'foo': raw})
            assert not form.get_errors(), 'input: %s' % raw
            f = form.fields_by_name['foo']
            if f.is_list:
                assert f.raw_data_list == raw
                assert f.value_list == initial
                if initial:
                    assert [type(x) for x in f.value_list] == [type(x) for x in initial]
            else:
                assert f.raw_data == raw
                assert f.value == initial
                assert type(f.value) == type(initial)

    def test_normalizing():
        for non_normalized, normalized in normalizing:
            form = Form(fields=[shortcut(required=True, name='foo')], data={'foo': non_normalized})
            assert not form.get_errors()
            assert form.fields_by_name['foo'].rendered_value() == normalized

    test_empty_string_data()
    test_empty_data()
    test_roundtrip_from_initial_to_raw_string()
    test_roundtrip_from_raw_string_to_initial()
    test_editable_false()
    test_normalizing()


def test_datetime():
    shortcut_test(
        Field.datetime,
        raw_and_parsed_data_tuples=[
            ('2001-02-03 12:13:14', datetime(2001, 2, 3, 12, 13, 14)),
        ],
        normalizing=[
            ('2001-02-03 12:13', '2001-02-03 12:13:00'),
            ('2001-02-03 12', '2001-02-03 12:00:00'),
        ],
    )


def test_date():
    shortcut_test(
        Field.date,
        raw_and_parsed_data_tuples=[
            ('2001-02-03', date(2001, 2, 3)),
        ],
    )


def test_time():
    shortcut_test(
        Field.time,
        raw_and_parsed_data_tuples=[
            ('12:34:56', time(12, 34, 56)),
        ],
        normalizing=[
            ('2:34:56', '02:34:56'),
        ],
    )


def test_integer():
    shortcut_test(
        Field.integer,
        raw_and_parsed_data_tuples=[
            ('123', 123),
        ],
        normalizing=[
            ('00123', '123'),
        ],
    )


def test_float():
    shortcut_test(
        Field.float,
        raw_and_parsed_data_tuples=[
            ('123.0', 123.0),
            ('123.123', 123.123),
        ],
        normalizing=[
            ('123', '123.0'),
            ('00123', '123.0'),
            ('00123.123', '123.123'),
        ],
    )


def test_render_custom():
    sentinel = '!!custom!!'
    assert sentinel in Form(fields=[
        Field(
            name='foo',
            initial='not sentinel value',
            render_value=lambda form, field, value: sentinel),
    ]).table()


def test_boolean_initial_true():
    fields = [Field.boolean(name='foo', initial=True), Field(name='bar', required=False)]

    form = Form(data={}, fields=fields)
    assert form.fields_by_name['foo'].value is True

    # If there are arguments, but not for key foo it means checkbox for foo has been unchecked.
    # Field foo should therefore be false.
    form = Form(data=dict(bar='baz', **{'-': '-'}), fields=fields)
    assert form.fields_by_name['foo'].value is False

    form = Form(data=dict(foo='on', bar='baz', **{'-': '-'}), fields=fields)
    assert form.fields_by_name['foo'].value is True


def test_file():
    class FooForm(Form):
        foo = Field.file(required=False)
    form = FooForm(data=dict(foo='1'))
    instance = Struct(foo=None)
    assert form.is_valid()
    form.apply(instance)
    assert instance.foo == '1'

    # Non-existent form entry should not overwrite data
    form = FooForm(data=dict(foo=''))
    assert form.is_valid(), {x.name: x.errors for x in form.fields}
    form.apply(instance)
    assert instance.foo == '1'

    form = FooForm(data={})
    assert form.is_valid(), {x.name: x.errors for x in form.fields}
    form.apply(instance)
    assert instance.foo == '1'


def test_mode_full_form_from_request():
    class FooForm(Form):
        foo = Field(required=True)
        bar = Field(required=True)
        baz = Field.boolean(initial=True)

    # empty POST
    form = FooForm(request=RequestFactory().post('/', {'-': '-'}))
    assert not form.is_valid()
    assert form.errors == set()
    assert form.fields_by_name['foo'].errors == {'This field is required'}
    assert form.fields_by_name['bar'].errors == {'This field is required'}
    assert form.fields_by_name['baz'].errors == set()  # not present in POST request means false

    form = FooForm(request=RequestFactory().post('/', {
        'foo': 'x',
        'bar': 'y',
        'baz': 'false',
        '-': '-',
    }))
    assert form.is_valid()
    assert form.fields_by_name['baz'].value is False

    # all params in GET
    form = FooForm(request=RequestFactory().get('/', {'-': '-'}))
    assert not form.is_valid()
    assert form.fields_by_name['foo'].errors == {'This field is required'}
    assert form.fields_by_name['bar'].errors == {'This field is required'}
    assert form.fields_by_name['baz'].errors == set()  # not present in POST request means false

    form = FooForm(request=RequestFactory().get('/', {
        'foo': 'x',
        'bar': 'y',
        'baz': 'on',
        '-': '-',
    }))
    assert not form.errors
    assert not form.fields[0].errors

    assert form.is_valid()


def test_mode_initials_from_get():
    class FooForm(Form):
        foo = Field(required=True)
        bar = Field(required=True)
        baz = Field.boolean(initial=True)

    # empty GET
    form = FooForm(request=RequestFactory().get('/'))
    assert form.is_valid()

    # initials from GET
    form = FooForm(request=RequestFactory().get('/', {'foo': 'foo_initial'}))
    assert form.is_valid()
    assert form.fields_by_name['foo'].value == 'foo_initial'

    assert form.fields_by_name['foo'].errors == set()
    assert form.fields_by_name['bar'].errors == set()
    assert form.fields_by_name['baz'].errors == set()


def test_form_errors_function():
    class MyForm(Form):
        foo = Field(is_valid=lambda **_: (False, 'field error'))

    def post_validation(form):
        form.add_error('global error')

    assert MyForm(request=RequestFactory().post('/', {'-': '-', 'foo': 'asd'}), post_validation=post_validation).get_errors() == {'global': {'global error'}, 'fields': {'foo': {'field error'}}}


def test_null_field_factory():
    class ShouldBeNullField(models.Field):
        pass

    class FooModel(models.Model):
        should_be_null = ShouldBeNullField()
        foo = models.IntegerField()

    register_field_factory(ShouldBeNullField, lambda **_: None)

    form = Form.from_model(data=None, model=FooModel)
    assert list(form.fields_by_name.keys()) == ['foo']


@pytest.mark.django_db
def test_choice_queryset_ajax():
    User.objects.create(username='foo')
    user2 = User.objects.create(username='bar')

    class MyForm(Form):
        username = Field.choice_queryset(choices=User.objects.all(), extra__endpoint_attr='username')
        not_returning_anything = Field.integer()

    form = MyForm(request=RequestFactory().get('/'))
    assert form.endpoint_dispatch(key='field__username', value='ar') == [{'id': user2.pk, 'text': smart_text(user2)}]
    assert form.endpoint_dispatch(key='field__not_returning_anything', value='ar') is None


def test_is_empty_form_marker():
    request = RequestFactory().get('/')
    assert AVOID_EMPTY_FORM in Form(request=request).render()
    assert AVOID_EMPTY_FORM not in Form(request=request, is_full_form=False).render()


def test_json_parsing():
    # NOTE: Parsing json input requires that any numbers that come in must avoid the string strip
    f = Form(data={'foo': 1}, fields=[Field.integer(name='foo', strip_input=False)])
    assert f.is_valid()
    assert f.fields_by_name['foo'].value == 1


def test_custom_endpoint():
    class MyForm(Form):
        class Meta:
            endpoint__foo = lambda key, value, **_: 'foo' + value

    form = MyForm(data={})
    assert 'foobar' == form.endpoint_dispatch(key='foo', value='bar')
