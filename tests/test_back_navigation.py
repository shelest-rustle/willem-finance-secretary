"""Кнопка «Назад» в пошаговых флоу: возвращает на предыдущий экран и сбрасывает данные
отменённого шага. Хендлеры вызываются напрямую с фейковыми CallbackQuery — без aiogram-
маршрутизации, проверяется только логика «куда возвращаться»."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from tests.test_edit import make_config
from willem.bot.handlers import expense, income, transfer
from willem.db import categories, sources
from willem.db.connection import connect, init_db
from willem.texts import Texts


class FakeMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object]] = []

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


class FakeUser:
    id = 1


class FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = FakeMessage()
        self.from_user = FakeUser()

    async def answer(self, *args, **kwargs) -> None:
        pass


def make_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def callback_data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


# --- расход ---


async def test_expense_back_from_comment_without_subcategories_goes_to_category(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(conn, 1, "Питание")
    state = make_state()
    await state.set_state(expense.ExpenseFlow.entering_comment)
    await state.update_data(amount=100.0, source_id=source.id, category_id=category.id)

    callback = FakeCallback(expense.BACK_CB)
    await expense.back_from_comment(callback, state, config, texts)

    assert await state.get_state() == expense.ExpenseFlow.choosing_category.state
    assert (await state.get_data())["category_id"] is None
    text, markup = callback.message.edits[-1]
    assert text == texts.get("common.choose_category")
    assert expense.BACK_CB in callback_data(markup)


async def test_expense_back_from_comment_with_subcategories_goes_to_subcategory(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(conn, 1, "Еда")
        sub = categories.create_category(conn, 1, "Продукты", parent_id=category.id)
    state = make_state()
    await state.set_state(expense.ExpenseFlow.entering_comment)
    await state.update_data(source_id=source.id, category_id=category.id, subcategory_id=sub.id)

    await expense.back_from_comment(FakeCallback(expense.BACK_CB), state, config, texts)

    assert await state.get_state() == expense.ExpenseFlow.choosing_subcategory.state
    data = await state.get_data()
    assert data["subcategory_id"] is None
    assert data["category_id"] == category.id


async def test_expense_back_from_comment_after_early_credit_payment_goes_to_overpayment(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    state = make_state()
    await state.set_state(expense.ExpenseFlow.entering_comment)
    await state.update_data(
        category_id="c", subcategory_id="s", credit_sheet_title="МТС",
        credit_payment_type=expense.CREDIT_TYPE_EARLY, credit_reduce_payment=True,
    )

    await expense.back_from_comment(FakeCallback(expense.BACK_CB), state, config, texts)

    assert await state.get_state() == expense.ExpenseFlow.choosing_credit_overpayment_action.state
    assert (await state.get_data())["credit_reduce_payment"] is None


async def test_expense_back_from_comment_after_regular_credit_payment_goes_to_payment_type(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    state = make_state()
    await state.set_state(expense.ExpenseFlow.entering_comment)
    await state.update_data(
        category_id="c", subcategory_id="s", credit_sheet_title="МТС",
        credit_payment_type=expense.CREDIT_TYPE_REGULAR,
    )

    await expense.back_from_comment(FakeCallback(expense.BACK_CB), state, config, texts)

    assert await state.get_state() == expense.ExpenseFlow.choosing_credit_payment_type.state
    assert (await state.get_data())["credit_payment_type"] is None


async def test_expense_first_step_offers_cancel(db_path: str, texts: Texts) -> None:
    config = make_config(db_path)
    with connect(db_path) as conn:
        sources.create_source(conn, 1, "Kaspi", "card", "KZT")
    state = make_state()
    await state.set_state(expense.ExpenseFlow.choosing_source)

    assert expense.CANCEL_CB in callback_data(expense._source_keyboard(config, 1))

    callback = FakeCallback(expense.CANCEL_CB)
    await expense.cancel_expense(callback, state, texts)
    assert await state.get_state() is None
    assert callback.message.edits[-1][0] == texts.get("common.cancelled")


# --- доход ---


async def test_income_back_from_comment_without_auto_category_goes_to_source(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
    state = make_state()
    await state.set_state(income.IncomeFlow.entering_comment)
    await state.update_data(amount=100.0, source_id=source.id)

    callback = FakeCallback(income.BACK_CB)
    await income.back_from_comment(callback, state, config, texts)

    assert await state.get_state() == income.IncomeFlow.choosing_source.state
    assert (await state.get_data())["source_id"] is None
    assert income.CANCEL_CB in callback_data(callback.message.edits[-1][1])


async def test_income_back_from_comment_with_auto_category_goes_to_subcategory(
    db_path: str, texts: Texts
) -> None:
    config = replace(make_config(db_path), auto_category={"income": "Доходы"})
    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(conn, 1, "Доходы")
        sub = categories.create_category(conn, 1, "Зарплата", parent_id=category.id)
    state = make_state()
    await state.set_state(income.IncomeFlow.entering_comment)
    await state.update_data(source_id=source.id, category_id=category.id, subcategory_id=sub.id)

    await income.back_from_comment(FakeCallback(income.BACK_CB), state, config, texts)

    assert await state.get_state() == income.IncomeFlow.choosing_subcategory.state
    assert (await state.get_data())["subcategory_id"] is None


# --- перевод ---


async def _transfer_state(db_path: str, from_currency: str, to_currency: str) -> FSMContext:
    with connect(db_path) as conn:
        source_from = sources.create_source(conn, 1, "Kaspi", "card", from_currency)
        source_to = sources.create_source(conn, 1, "Т-банк", "card", to_currency)
    state = make_state()
    await state.set_state(transfer.TransferFlow.entering_comment)
    await state.update_data(
        amount=1000.0, source_from_id=source_from.id, source_to_id=source_to.id,
        final_target_amount=180.0,
    )
    return state


async def test_transfer_back_from_comment_cross_currency_goes_to_target_amount(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    state = await _transfer_state(db_path, "KZT", "RUB")

    await transfer.back_from_comment(FakeCallback(transfer.BACK_CB), state, config, texts)

    assert await state.get_state() == transfer.TransferFlow.entering_target_amount.state
    assert (await state.get_data())["final_target_amount"] is None


async def test_transfer_back_from_comment_same_currency_goes_to_target_source(
    db_path: str, texts: Texts
) -> None:
    config = make_config(db_path)
    state = await _transfer_state(db_path, "KZT", "KZT")

    await transfer.back_from_comment(FakeCallback(transfer.BACK_CB), state, config, texts)

    assert await state.get_state() == transfer.TransferFlow.choosing_source_to.state
    assert (await state.get_data())["source_to_id"] is None


async def test_transfer_back_from_source_from_returns_to_amount_with_cancel(texts: Texts) -> None:
    state = make_state()
    await state.set_state(transfer.TransferFlow.choosing_source_from)

    callback = FakeCallback(transfer.BACK_CB)
    await transfer.back_to_amount(callback, state, texts)

    assert await state.get_state() == transfer.TransferFlow.entering_amount.state
    text, markup = callback.message.edits[-1]
    assert text == texts.get("transfer.enter_amount")
    assert callback_data(markup) == [transfer.CANCEL_CB]
