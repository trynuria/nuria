"""Learn reusable AST fragments with Stitch; execute only the existing language.

The learned state is data, not Python. Learning changes the search vocabulary,
not the interpreter, its limits, or the authority to accept a proposed answer.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
import time
from decimal import InvalidOperation

from cognition.skills import WorkLimit, _Meter
from cognition.text_composition import infer_composed
from cognition.text_skills import (
    DEFAULT_TEXT_CONFIG,
    VERSION,
    evaluate,
    execute_checked,
    fields,
    normalize_config,
    shape,
    text,
    validate_program,
)

LIBRARY_VERSION = "stitch-text-concepts-v1"


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def encode(node):
    """Keep operation parameters atomic; abstractions bind expression holes."""
    metadata, children, layout = {}, [], []
    for key, value in sorted(node.items()):
        if isinstance(value, dict):
            layout.append([key, 1])
            children.append(encode(value))
        elif isinstance(value, list):
            layout.append([key, len(value)])
            children.extend(encode(child) for child in value)
        else:
            metadata[key] = value
    head = "p" + serial([metadata, layout]).encode().hex()
    return f"({head} {' '.join(children)})" if children else head


def parse(source):
    if type(source) is not str or len(source) > 32768:
        raise ValueError("Concept term exceeds its input bound")
    tokens = re.findall(r"\(|\)|[^\s()]+", source)
    index = 0

    def term(depth=0):
        nonlocal index
        if index >= len(tokens) or depth > 32:
            raise ValueError("Malformed or deeply nested concept")
        token = tokens[index]
        index += 1
        if token == "(":
            result = []
            while index < len(tokens) and tokens[index] != ")":
                result.append(term(depth + 1))
            if not result or index >= len(tokens):
                raise ValueError("Unclosed or empty concept")
            index += 1
            return result
        if token == ")":
            raise ValueError("Unexpected concept delimiter")
        return token

    result = term()
    if index != len(tokens):
        raise ValueError("Trailing concept data")
    return result


def substitute(term, arguments):
    if isinstance(term, str):
        if term.startswith("#"):
            if not re.fullmatch(r"#[0-9]+", term):
                raise ValueError("Invalid concept parameter")
            index = int(term[1:])
            if index >= len(arguments):
                raise ValueError("Concept parameter is out of range")
            return copy.deepcopy(arguments[index])
        return term
    return [substitute(child, arguments) for child in term]


def decode(term, definitions=(), *, depth=0, remaining=None):
    """Expand learned functions before the unchanged interpreter sees a node."""
    if remaining is None:
        remaining = [512]
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 32:
        raise ValueError("Concept expansion exceeds its bound")
    head, arguments = (term, []) if isinstance(term, str) else (term[0], term[1:])
    if not isinstance(head, str):
        raise ValueError("Higher-order concepts are unsupported")
    if head in definitions:
        body, arity = definitions[head]
        if len(arguments) != arity:
            raise ValueError("Concept call has the wrong arity")
        return decode(
            substitute(body, arguments),
            definitions,
            depth=depth + 1,
            remaining=remaining,
        )
    if not head.startswith("p"):
        raise ValueError("Concept references an unknown primitive")
    try:
        metadata, layout = json.loads(bytes.fromhex(head[1:]))
    except (ValueError, TypeError) as error:
        raise ValueError("Malformed concept primitive") from error
    if type(metadata) is not dict or type(layout) is not list:
        raise ValueError("Malformed concept layout")
    result, offset = copy.deepcopy(metadata), 0
    for key, count in layout:
        if key not in ("arg", "parts", "then", "else") or type(count) is not int:
            raise ValueError("Unknown concept child")
        if not 1 <= count <= 6 or offset + count > len(arguments) or key in result:
            raise ValueError("Malformed concept children")
        values = [
            decode(child, definitions, depth=depth + 1, remaining=remaining)
            for child in arguments[offset : offset + count]
        ]
        result[key] = values if key == "parts" else values[0]
        if key != "parts" and count != 1:
            raise ValueError("Unary concept child has multiple values")
        offset += count
    if offset != len(arguments):
        raise ValueError("Primitive has extra arguments")
    return result


class ConceptLibrary:
    def __init__(self, state, config=DEFAULT_TEXT_CONFIG):
        self.config = normalize_config(config)
        if type(state) is not dict or state.get("version") != LIBRARY_VERSION:
            raise ValueError("Unsupported concept library")
        if len(serial(state)) > 2_000_000:
            raise ValueError("Concept library exceeds its bound")
        if len(state["concepts"]) > 32 or len(state["programs"]) > 512:
            raise ValueError("Concept library has too many entries")
        self.state = copy.deepcopy(state)
        self.programs = [validate_program(p, self.config) for p in state["programs"]]
        if state["training_sha256"] != digest(self.programs):
            raise ValueError("Concept training commitment changed")
        self.definitions = {}
        for concept in state["concepts"]:
            name, arity = concept["name"], concept["arity"]
            if name in self.definitions or not re.fullmatch(r"fn_[0-9]+", name):
                raise ValueError("Invalid or repeated concept name")
            if type(arity) is not int or not 0 <= arity <= 2:
                raise ValueError("Concept arity exceeds its bound")
            body = parse(concept["body"])
            # Sequential definitions forbid recursion and future references.
            probes = [parse(encode({"op": "field", "index": i})) for i in range(arity)]
            node = decode(substitute(body, probes), self.definitions)
            validate_program({"version": VERSION, "expr": node}, self.config)
            self.definitions[name] = body, arity

    @classmethod
    def learn(cls, programs, *, iterations=12, config=DEFAULT_TEXT_CONFIG):
        """Learn from caller-verified programs; no task targets enter Stitch."""
        import stitch_core

        config = normalize_config(config)
        if type(iterations) is not int or not 1 <= iterations <= 32:
            raise ValueError("Learning iterations exceed their bound")
        programs = sorted({serial(validate_program(p, config)) for p in programs})
        if not 2 <= len(programs) <= 512:
            raise ValueError("Learning needs 2 to 512 distinct verified programs")
        programs = [json.loads(p) for p in programs]
        terms = [encode(p["expr"]) for p in programs]
        started = time.process_time()
        result = stitch_core.compress(
            terms,
            iterations=iterations,
            max_arity=2,
            threads=1,
            silent=True,
            no_curried_bodies=True,
            no_curried_metavars=True,
        )
        state = {
            "version": LIBRARY_VERSION,
            "programs": programs,
            "training_sha256": digest(programs),
            "concepts": [
                {"name": a.name, "body": a.body, "arity": a.arity}
                for a in result.abstractions
            ],
            "learning_cpu_seconds": time.process_time() - started,
            "compression": {
                key: result.json[key]
                for key in ("original_cost", "final_cost", "compression_ratio")
            },
        }
        library = cls(state, config)
        # Exact AST reconstruction is stronger than checking a few outputs.
        if [decode(parse(term), library.definitions) for term in result.rewritten] != [
            p["expr"] for p in programs
        ]:
            raise ValueError("Learned library changes a training program")
        return library

    @classmethod
    def learn_checked(cls, experiences, *, previous=None, config=DEFAULT_TEXT_CONFIG):
        """Recheck supplied examples before retention; finite checks are not proof.

        A previous library retains earlier programs. Feedback must come from the
        calling application, not from the learner inventing its own acceptance.
        No claim of authenticated external provenance is made by this API.
        """
        if type(experiences) is not list or not 1 <= len(experiences) <= 512:
            raise ValueError("Learning needs bounded checked experiences")
        programs = list(previous.programs) if previous is not None else []
        for experience in experiences:
            program = validate_program(experience["program"], config)
            inputs, outputs = experience["inputs"], experience["outputs"]
            if not 1 <= len(inputs) == len(outputs) <= 128:
                raise ValueError("Experience needs bounded paired check rows")
            for value, target in zip(inputs, outputs, strict=True):
                checked = execute_checked(program, value, config)
                if checked["error"] is not None or checked["output"] != target:
                    raise ValueError("An experience failed its supplied checks")
            programs.append(program)
        return cls.learn(programs, config=config)

    def infer_concepts(self, rows, targets, meter):
        """Bottom-up observationally deduplicated composition of learned rules.

        A rule can consume another rule's output, so this searches programs
        absent from both the retained library and the original shallow solver.
        Equality on examples only prunes search; it never certifies a result.
        """
        atoms, signatures = [], set()

        def attempt(node):
            if meter.values["candidates"] >= self.config["max_candidates"]:
                raise WorkLimit("Concept candidate limit reached")
            meter.charge("candidates")
            meter.data(serial(node))
            try:
                shape(node, self.config)
                values = tuple(evaluate(node, row, meter, self.config) for row in rows)
            except (ValueError, IndexError, KeyError, InvalidOperation) as error:
                if isinstance(error, WorkLimit):
                    raise
                meter.values["errors"] += 1
                return False
            meter.data(serial(values))
            if values == targets:
                return True
            if values not in signatures and len(atoms) < 256:
                signatures.add(values)
                atoms.append(parse(encode(node)))
            else:
                meter.values["semantic_duplicates"] += 1
            return False

        for index in range(min(map(len, rows))):
            seed = {"op": "field", "index": index}
            if attempt(seed):
                return seed
        previous = 0
        rules = sorted(self.definitions.values(), key=lambda item: item[1])
        for _ in range(3):
            count = len(atoms)
            if count == previous:
                break
            for body, arity in rules:
                for indices in itertools.product(range(count), repeat=arity):
                    if indices and max(indices) < previous:
                        continue
                    if not indices and previous:
                        continue
                    try:
                        node = decode(
                            substitute(body, [atoms[i] for i in indices]),
                            self.definitions,
                        )
                    except ValueError:
                        meter.charge("candidates")
                        continue
                    if attempt(node):
                        return node
            previous = count
        return None

    def solve(self, inputs, outputs, *, budget=50000, mode="concepts"):
        """Equal total deterministic budget for learned, retained, and cold search.

        Every learned candidate is fully expanded and charged by the old
        interpreter. A bounded prefix precedes the original composed solver;
        unused prefix work stays available to it. Returned programs are proposals.
        """
        if mode not in ("concepts", "retained", "cold"):
            raise ValueError("Unknown learning comparison")
        if (
            type(inputs) is not list
            or type(outputs) is not list
            or not 1 <= len(inputs) == len(outputs) <= self.config["max_rows"]
        ):
            raise ValueError("Search needs bounded paired examples")
        rows = [fields(value, self.config) for value in inputs]
        meter = _Meter(budget)
        targets = tuple(
            text(value, self.config["max_input_bytes"]) for value in outputs
        )
        prefix_limit = min(10000, max(1, budget // 3))
        prefix_work, selected, source = 0, None, "abstained"
        started = time.process_time()
        try:
            if mode != "cold":
                meter.limit = prefix_limit
                try:
                    # Charge library retrieval as well as expanded execution.
                    meter.data(
                        serial(
                            self.state["concepts"]
                            if mode == "concepts"
                            else self.programs
                        )
                    )
                    if mode == "concepts":
                        selected = self.infer_concepts(rows, targets, meter)
                    else:
                        for program in self.programs:
                            node = program["expr"]
                            if (
                                meter.values["candidates"]
                                >= self.config["max_candidates"]
                            ):
                                raise WorkLimit("Retained candidate limit reached")
                            meter.charge("candidates")
                            meter.data(serial(node))
                            try:
                                values = tuple(
                                    evaluate(node, row, meter, self.config)
                                    for row in rows
                                )
                            except (
                                ValueError,
                                IndexError,
                                KeyError,
                                InvalidOperation,
                            ) as error:
                                if isinstance(error, WorkLimit):
                                    raise
                                meter.values["errors"] += 1
                            else:
                                meter.data(serial(values))
                                if values == targets:
                                    selected = node
                                    break
                except WorkLimit:
                    pass
                finally:
                    meter.limit = budget
                if selected is not None:
                    source = mode
            prefix_work = meter.values["total"]
            if selected is None:
                selected = infer_composed(inputs, outputs, self.config, meter)
                if selected is not None:
                    source = "composed"
        except WorkLimit:
            pass
        return {
            "program": {"version": VERSION, "expr": selected}
            if selected is not None
            else None,
            "source": source,
            "work": meter.values,
            "prefix_work": prefix_work,
            "cpu_seconds": time.process_time() - started,
        }


def main():
    """Local JSON interface; no model API, network, credentials, or daemon."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    learn = commands.add_parser("learn", help="Learn from supplied checked experiences")
    learn.add_argument("experiences", type=Path)
    learn.add_argument("output", type=Path)
    learn.add_argument("--previous", type=Path)
    solve = commands.add_parser(
        "solve", help="Propose a program from input/output examples"
    )
    solve.add_argument("library", type=Path)
    solve.add_argument("examples", type=Path)
    solve.add_argument("--budget", type=int, default=50000)
    solve.add_argument(
        "--mode", choices=("concepts", "retained", "cold"), default="concepts"
    )
    apply = commands.add_parser(
        "apply", help="Execute a proposal using the bounded interpreter"
    )
    apply.add_argument("proposal", type=Path)
    apply.add_argument("inputs", type=Path)
    args = parser.parse_args()

    def read(path):
        with path.open("rb") as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Input file exceeds its byte bound")
        return json.loads(raw)

    if args.command == "learn":
        previous = ConceptLibrary(read(args.previous)) if args.previous else None
        library = ConceptLibrary.learn_checked(
            read(args.experiences), previous=previous
        )
        # A failed/new learning run cannot overwrite an earlier usable library.
        with args.output.open("x") as stream:
            stream.write(serial(library.state) + "\n")
        print(
            serial(
                {
                    "programs": len(library.programs),
                    "concepts": len(library.definitions),
                }
            )
        )
    elif args.command == "solve":
        library, examples = ConceptLibrary(read(args.library)), read(args.examples)
        result = library.solve(
            examples["inputs"], examples["outputs"], budget=args.budget, mode=args.mode
        )
        print(
            serial(
                {
                    **result,
                    "status": "unverified_proposal"
                    if result["program"]
                    else "abstained",
                }
            )
        )
    else:
        proposal, values = read(args.proposal), read(args.inputs)
        program = proposal.get("program", proposal)
        if type(values) is not list or len(values) > 128:
            raise ValueError("Execution needs a bounded input list")
        print(serial([execute_checked(program, value) for value in values]))


if __name__ == "__main__":
    main()
