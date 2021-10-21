import abc
import datetime
import decimal
from collections import namedtuple
from typing import List, Optional
from unittest import mock

import pytest
import redis
from pydantic import ValidationError

from redis_developer.model import EmbeddedJsonModel, Field, JsonModel
from redis_developer.model.migrations.migrator import Migrator
from redis_developer.model.model import (
    NotFoundError,
    QueryNotSupportedError,
    RedisModelError,
)


today = datetime.date.today()


@pytest.fixture
def m(key_prefix):
    class BaseJsonModel(JsonModel, abc.ABC):
        class Meta:
            global_key_prefix = key_prefix


    class Note(EmbeddedJsonModel):
        # TODO: This was going to be a full-text search example, but
        #  we can't index embedded documents for full-text search in
        #  the preview release.
        description: str = Field(index=True)
        created_on: datetime.datetime


    class Address(EmbeddedJsonModel):
        address_line_1: str
        address_line_2: Optional[str]
        city: str = Field(index=True)
        state: str
        country: str
        postal_code: str = Field(index=True)
        note: Optional[Note]


    class Item(EmbeddedJsonModel):
        price: decimal.Decimal
        name: str = Field(index=True)


    class Order(EmbeddedJsonModel):
        items: List[Item]
        created_on: datetime.datetime


    class Member(BaseJsonModel):
        first_name: str = Field(index=True)
        last_name: str = Field(index=True)
        email: str = Field(index=True)
        join_date: datetime.date
        age: int = Field(index=True)
        bio: Optional[str] = Field(index=True, full_text_search=True, default="")

        # Creates an embedded model.
        address: Address

        # Creates an embedded list of models.
        orders: Optional[List[Order]]

    Migrator().run()

    return namedtuple('Models', ['BaseJsonModel', 'Note', 'Address', 'Item', 'Order', 'Member'])(
        BaseJsonModel, Note, Address, Item, Order, Member)


@pytest.fixture()
def address(m):
    yield m.Address(
        address_line_1="1 Main St.",
        city="Portland",
        state="OR",
        country="USA",
        postal_code=11111,
    )


@pytest.fixture()
def members(address, m):
    member1 = m.Member(
        first_name="Andrew",
        last_name="Brookins",
        email="a@example.com",
        age=38,
        join_date=today,
        address=address,
    )

    member2 = m.Member(
        first_name="Kim",
        last_name="Brookins",
        email="k@example.com",
        age=34,
        join_date=today,
        address=address,
    )

    member3 = m.Member(
        first_name="Andrew",
        last_name="Smith",
        email="as@example.com",
        age=100,
        join_date=today,
        address=address,
    )

    member1.save()
    member2.save()
    member3.save()

    yield member1, member2, member3


def test_validates_required_fields(address, m):
    # Raises ValidationError address is required
    with pytest.raises(ValidationError):
        m.Member(
            first_name="Andrew",
            last_name="Brookins",
            zipcode="97086",
            join_date=today,
        )


def test_validates_field(address, m):
    # Raises ValidationError: join_date is not a date
    with pytest.raises(ValidationError):
        m.Member(
            first_name="Andrew",
            last_name="Brookins",
            join_date="yesterday",
            address=address,
        )


# Passes validation
def test_validation_passes(address, m):
    member = m.Member(
        first_name="Andrew",
        last_name="Brookins",
        email="a@example.com",
        join_date=today,
        age=38,
        address=address,
    )
    assert member.first_name == "Andrew"


def test_saves_model_and_creates_pk(address, m):
    member = m.Member(
        first_name="Andrew",
        last_name="Brookins",
        email="a@example.com",
        join_date=today,
        age=38,
        address=address,
    )
    # Save a model instance to Redis
    member.save()

    member2 = m.Member.get(member.pk)
    assert member2 == member
    assert member2.address == address


@pytest.mark.skip("Not implemented yet")
def test_saves_many(address, m):
    members = [
        m.Member(
            first_name="Andrew",
            last_name="Brookins",
            email="a@example.com",
            join_date=today,
            address=address,
            age=38,
        ),
        m.Member(
            first_name="Kim",
            last_name="Brookins",
            email="k@example.com",
            join_date=today,
            address=address,
            age=34,
        ),
    ]
    m.Member.add(members)


@pytest.mark.skip("Not ready yet")
def test_updates_a_model(members, m):
    member1, member2, member3 = members

    # Or, with an implicit save:
    member1.update(last_name="Smith")
    assert m.Member.find(m.Member.pk == member1.pk).first() == member1

    # Or, affecting multiple model instances with an implicit save:
    m.Member.find(m.Member.last_name == "Brookins").update(last_name="Smith")
    results = m.Member.find(m.Member.last_name == "Smith")
    assert results == members

    # Or, updating a field in an embedded model:
    member2.update(address__city="Happy Valley")
    assert m.Member.find(m.Member.pk == member2.pk).first().address.city == "Happy Valley"


def test_paginate_query(members, m):
    member1, member2, member3 = members
    actual = m.Member.find().sort_by('age').all(batch_size=1)
    assert actual == [member2, member1, member3]


def test_access_result_by_index_cached(members, m):
    member1, member2, member3 = members
    query = m.Member.find().sort_by("age")
    # Load the cache, throw away the result.
    assert query._model_cache == []
    query.execute()
    assert query._model_cache == [member2, member1, member3]

    # Access an item that should be in the cache.
    with mock.patch.object(query.model, "db") as mock_db:
        assert query[0] == member2
        assert not mock_db.called


def test_access_result_by_index_not_cached(members, m):
    member1, member2, member3 = members
    query = m.Member.find().sort_by("age")

    # Assert that we don't have any models in the cache yet -- we
    # haven't made any requests of Redis.
    assert query._model_cache == []
    assert query[0] == member2
    assert query[1] == member1
    assert query[2] == member3


def test_in_query(members, m):
    member1, member2, member3 = members
    actual = m.Member.find(m.Member.pk << [member1.pk, member2.pk, member3.pk]).sort_by('age').all()
    assert actual == [member2, member1, member3]


@pytest.mark.skip("Not implemented yet")
def test_update_query(members, m):
    member1, member2, member3 = members
    m.Member.find(m.Member.pk << [member1.pk, member2.pk, member3.pk]).update(
        first_name="Bobby"
    )
    actual = (
        m.Member.find(m.Member.pk << [member1.pk, member2.pk, member3.pk])
        .sort_by("age")
        .all()
    )
    assert actual == [member1, member2, member3]
    assert all([m.name == "Bobby" for m in actual])


def test_exact_match_queries(members, m):
    member1, member2, member3 = members

    actual = m.Member.find(m.Member.last_name == "Brookins").sort_by('age').all()
    assert actual == [member2, member1]

    actual = m.Member.find(
        (m.Member.last_name == "Brookins") & ~(m.Member.first_name == "Andrew")
    ).all()
    assert actual == [member2]

    actual = m.Member.find(~(m.Member.last_name == "Brookins")).all()
    assert actual == [member3]

    actual = m.Member.find(m.Member.last_name != "Brookins").all()
    assert actual == [member3]

    actual = m.Member.find(
        (m.Member.last_name == "Brookins") & (m.Member.first_name == "Andrew")
        | (m.Member.first_name == "Kim")
    ).sort_by('age').all()
    assert actual == [member2, member1]

    actual = m.Member.find(
        m.Member.first_name == "Kim", m.Member.last_name == "Brookins"
    ).all()
    assert actual == [member2]

    actual = m.Member.find(m.Member.address.city == "Portland").sort_by('age').all()
    assert actual == [member2, member1, member3]


def test_recursive_query_expression_resolution(members, m):
    member1, member2, member3 = members

    actual = m.Member.find(
        (m.Member.last_name == "Brookins")
        | (m.Member.age == 100) & (m.Member.last_name == "Smith")
    ).sort_by('age').all()
    assert actual == [member2, member1, member3]


def test_recursive_query_field_resolution(members, m):
    member1, _, _ = members
    member1.address.note = m.Note(
        description="Weird house", created_on=datetime.datetime.now()
    )
    member1.save()
    actual = m.Member.find(m.Member.address.note.description == "Weird house").all()
    assert actual == [member1]

    member1.orders = [
        m.Order(
            items=[m.Item(price=10.99, name="Ball")],
            total=10.99,
            created_on=datetime.datetime.now(),
        )
    ]
    member1.save()
    actual = m.Member.find(m.Member.orders.items.name == "Ball").all()
    assert actual == [member1]
    assert actual[0].orders[0].items[0].name == "Ball"


def test_full_text_search(members, m):
    member1, member2, _ = members
    member1.update(bio="Hates sunsets, likes beaches")
    member2.update(bio="Hates beaches, likes forests")

    actual = m.Member.find(m.Member.bio % "beaches").sort_by('age').all()
    assert actual == [member2, member1]

    actual = m.Member.find(m.Member.bio % "forests").all()
    assert actual == [member2]


def test_tag_queries_boolean_logic(members, m):
    member1, member2, member3 = members

    actual = m.Member.find(
        (m.Member.first_name == "Andrew") & (m.Member.last_name == "Brookins")
        | (m.Member.last_name == "Smith")
    ).sort_by('age').all()
    assert actual == [member1, member3]


def test_tag_queries_punctuation(address, m):
    member1 = m.Member(
        first_name="Andrew, the Michael",
        last_name="St. Brookins-on-Pier",
        email="a|b@example.com",  # NOTE: This string uses the TAG field separator.
        age=38,
        join_date=today,
        address=address,
    )
    member1.save()

    member2 = m.Member(
        first_name="Bob",
        last_name="the Villain",
        email="a|villain@example.com",  # NOTE: This string uses the TAG field separator.
        age=38,
        join_date=today,
        address=address,
    )
    member2.save()

    assert m.Member.find(m.Member.first_name == "Andrew, the Michael").first() == member1
    assert m.Member.find(m.Member.last_name == "St. Brookins-on-Pier").first() == member1

    # Notice that when we index and query multiple values that use the internal
    # TAG separator for single-value exact-match fields, like an indexed string,
    # the queries will succeed. We apply a workaround that queries for the union
    # of the two values separated by the tag separator.
    assert m.Member.find(m.Member.email == "a|b@example.com").all() == [member1]
    assert m.Member.find(m.Member.email == "a|villain@example.com").all() == [member2]


def test_tag_queries_negation(members, m):
    member1, member2, member3 = members

    """
           ┌first_name
     NOT EQ┤
           └Andrew

    """
    query = m.Member.find(~(m.Member.first_name == "Andrew"))
    assert query.all() == [member2]

    """
               ┌first_name
        ┌NOT EQ┤
        |      └Andrew
     AND┤
        |  ┌last_name
        └EQ┤
           └Brookins

    """
    query = m.Member.find(
        ~(m.Member.first_name == "Andrew") & (m.Member.last_name == "Brookins")
    )
    assert query.all() == [member2]

    """
               ┌first_name
        ┌NOT EQ┤
        |      └Andrew
     AND┤
        |     ┌last_name
        |  ┌EQ┤
        |  |  └Brookins
        └OR┤
           |  ┌last_name
           └EQ┤
              └Smith
    """
    query = m.Member.find(
        ~(m.Member.first_name == "Andrew")
        & ((m.Member.last_name == "Brookins") | (m.Member.last_name == "Smith"))
    )
    assert query.all() == [member2]

    """
                  ┌first_name
           ┌NOT EQ┤
           |      └Andrew
       ┌AND┤
       |   |  ┌last_name
       |   └EQ┤
       |      └Brookins
     OR┤
       |  ┌last_name
       └EQ┤
          └Smith
    """
    query = m.Member.find(
        ~(m.Member.first_name == "Andrew") & (m.Member.last_name == "Brookins")
        | (m.Member.last_name == "Smith")
    )
    assert query.sort_by('age').all() == [member2, member3]

    actual = m.Member.find(
        (m.Member.first_name == "Andrew") & ~(m.Member.last_name == "Brookins")
    ).all()
    assert actual == [member3]


def test_numeric_queries(members, m):
    member1, member2, member3 = members

    actual = m.Member.find(m.Member.age == 34).all()
    assert actual == [member2]

    actual = m.Member.find(m.Member.age > 34).all()
    assert actual == [member1, member3]

    actual = m.Member.find(m.Member.age < 35).all()
    assert actual == [member2]

    actual = m.Member.find(m.Member.age <= 34).all()
    assert actual == [member2]

    actual = m.Member.find(m.Member.age >= 100).all()
    assert actual == [member3]

    actual = m.Member.find(~(m.Member.age == 100)).sort_by('age').all()
    assert actual == [member2, member1]

    actual = m.Member.find(m.Member.age > 30, m.Member.age < 40).sort_by('age').all()
    assert actual == [member2, member1]

    actual = m.Member.find(m.Member.age != 34).sort_by('age').all()
    assert actual == [member1, member3]


def test_sorting(members, m):
    member1, member2, member3 = members

    actual = m.Member.find(m.Member.age > 34).sort_by("age").all()
    assert actual == [member1, member3]

    actual = m.Member.find(m.Member.age > 34).sort_by("-age").all()
    assert actual == [member3, member1]

    with pytest.raises(QueryNotSupportedError):
        # This field does not exist.
        m.Member.find().sort_by("not-a-real-field").all()

    with pytest.raises(QueryNotSupportedError):
        # This field is not sortable.
        m.Member.find().sort_by("join_date").all()


def test_not_found(m):
    with pytest.raises(NotFoundError):
        # This ID does not exist.
        m.Member.get(1000)


@pytest.mark.skip("Does not clean up after itself properly")
def test_list_field_limitations(m):
    with pytest.raises(RedisModelError):

        class SortableTarotWitch(m.BaseJsonModel):
            # We support indexing lists of strings for quality and membership
            # queries. Sorting is not supported, but is planned.
            tarot_cards: List[str] = Field(index=True, sortable=True)

    with pytest.raises(RedisModelError):

        class SortableFullTextSearchAlchemicalWitch(BaseJsonModel):
            # We don't support indexing a list of strings for full-text search
            # queries. Support for this feature is not planned.
            potions: List[str] = Field(index=True, full_text_search=True)

    with pytest.raises(RedisModelError):

        class NumerologyWitch(m.BaseJsonModel):
            # We don't support indexing a list of numbers. Support for this
            # feature is To Be Determined.
            lucky_numbers: List[int] = Field(index=True)

    with pytest.raises(RedisModelError):

        class ReadingWithPrice(EmbeddedJsonModel):
            gold_coins_charged: int = Field(index=True)

        class TarotWitchWhoCharges(m.BaseJsonModel):
            tarot_cards: List[str] = Field(index=True)

            # The preview release does not support indexing numeric fields on models
            # found within a list or tuple. This is the same limitation that stops
            # us from indexing plain lists (or tuples) containing numeric values.
            # The fate of this feature is To Be Determined.
            readings: List[ReadingWithPrice]

    class TarotWitch(m.BaseJsonModel):
        # We support indexing lists of strings for quality and membership
        # queries. Sorting is not supported, but is planned.
        tarot_cards: List[str] = Field(index=True)

    # We need to import and run this manually because we defined
    # our model classes within a function that runs after the test
    # suite's migrator has already looked for migrations to run.
    Migrator().run()

    witch = TarotWitch(tarot_cards=["death"])
    witch.save()
    actual = TarotWitch.find(TarotWitch.tarot_cards << "death").all()
    assert actual == [witch]


def test_schema(m, key_prefix):
    assert (
        m.Member.redisearch_schema()
        == f"ON JSON PREFIX 1 {key_prefix}:tests.test_json_model.Member: SCHEMA $.pk AS pk TAG SEPARATOR | $.first_name AS first_name TAG SEPARATOR | $.last_name AS last_name TAG SEPARATOR | $.email AS email TAG SEPARATOR |  $.age AS age NUMERIC $.bio AS bio TAG SEPARATOR | $.bio AS bio_fts TEXT $.address.pk AS address_pk TAG SEPARATOR | $.address.city AS address_city TAG SEPARATOR | $.address.postal_code AS address_postal_code TAG SEPARATOR | $.address.note.pk AS address_note_pk TAG SEPARATOR | $.address.note.description AS address_note_description TAG SEPARATOR | $.orders[*].pk AS orders_pk TAG SEPARATOR | $.orders[*].items[*].pk AS orders_items_pk TAG SEPARATOR | $.orders[*].items[*].name AS orders_items_name TAG SEPARATOR |"
    )
