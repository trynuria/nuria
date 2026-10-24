"""Explicit dispatch between reviewed data-only skill languages."""

from cognition import skills, text_skills


def normalize_config(config):
    if type(config) is dict and config.get("version") == text_skills.VERSION:
        return text_skills.normalize_config(config)
    return skills.normalize_config(config)


def library_factory(source_id, config, state=None):
    config = normalize_config(config)
    if config["version"] == text_skills.VERSION:
        return text_skills.TextSkillLibrary(source_id, config, state)
    return skills.SkillLibrary(source_id, config, state)


def execute_checked(program, value, config, budget=10000):
    if config.get("version") == text_skills.VERSION:
        return text_skills.execute_checked(program, value, config, budget)
    if config.get("version") == "safe-expression-v1":
        from cognition.skill_expressions import execute_checked as expression_execute

        return expression_execute(program, value, config, budget)
    return skills.execute_checked(program, value, config, budget)


def validate_program(program, config):
    if config.get("version") == text_skills.VERSION:
        return text_skills.validate_program(program, config)
    if config.get("version") == "safe-expression-v1":
        from cognition.skill_expressions import validate_program as expression_validate

        return expression_validate(program, config)
    return skills.validate_program(program, config)


def execution_work_ceiling(config, rows):
    """Conservative interpreter bound, including rejected text expansion.

    Valid text values are bounded before returning to a parent. A single failed
    replacement can expand one child by at most sixteen before rejection. JSON
    parsing, shape checking and library bookkeeping are measured in process CPU;
    these units bound the declared interpreter meter, not machine instructions.
    """
    config = normalize_config(config)
    if type(rows) is not int or not 1 <= rows <= config["max_rows"]:
        raise ValueError("Execution row bound is invalid")
    blocks = (config["max_input_bytes"] + 31) // 32
    if config["version"] == text_skills.VERSION:
        per_row = blocks + config["max_ops"] * (2 + blocks + 16 * blocks)
    else:
        per_row = config["max_ops"] * (1 + 2 * max(1, blocks))
    return rows * per_row
