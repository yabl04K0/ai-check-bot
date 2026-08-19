"""Провайдер-абстракция ИИ (Claude/Codex/Cursor/локальная LLM).

Тип задачи (app.tasks.types.TaskType) и провайдер — два независимых
измерения, см. README. Код не должен нигде хардкодить Claude как
единственный вариант — всё идёт через AIProvider (base.py) и registry.py.
"""
