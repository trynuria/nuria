import json
import math


def make_patch(old, new):
    ops = []
    path = []
    budget = 4

    def pointer():
        return "".join("/" + str(k).replace("~", "~0").replace("/", "~1") for k in path)

    def emit(kind, value=None):
        op = {"op": kind, "path": pointer()}
        if kind != "remove":
            op["value"] = value
        ops.append(op)

    def equal(a, b):
        if a is b:
            return True
        t = type(a)
        if t is not type(b):
            return False
        if t is dict:
            if len(a) != len(b):
                return False
            for k, v in a.items():
                if k not in b or not equal(v, b[k]):
                    return False
            return True
        if t is list:
            if len(a) != len(b):
                return False
            return all(equal(x, y) for x, y in zip(a, b))
        if t is float:
            return a == b and (
                a != 0.0 or math.copysign(1.0, a) == math.copysign(1.0, b)
            )
        return a == b

    def size(value):
        return len(
            json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        )

    def fallback(start, value):
        nonlocal budget
        if len(ops) - start < 32 or budget == 0:
            return
        budget -= 1
        replacement = {"op": "replace", "path": pointer(), "value": value}
        # Demand a margin because gzip compresses repeated patch syntax well.
        if size(replacement) * 4 < size(ops[start:]) * 3:
            del ops[start:]
            ops.append(replacement)

    def walk(a, b):
        if a is b:
            return
        ta, tb = type(a), type(b)
        start = len(ops)
        if ta is dict and tb is dict:
            for k in a:
                if k not in b:
                    path.append(k)
                    emit("remove")
                    path.pop()
            for k, value in b.items():
                if k not in a:
                    path.append(k)
                    emit("add", value)
                    path.pop()
                    continue
                previous = a[k]
                if type(previous) not in (dict, list) and type(value) not in (
                    dict,
                    list,
                ):
                    if equal(previous, value):
                        continue
                path.append(k)
                walk(previous, value)
                path.pop()
            fallback(start, b)
            return
        if ta is list and tb is list:
            na, nb = len(a), len(b)
            lo = 0
            limit = min(na, nb)
            while lo < limit and equal(a[lo], b[lo]):
                lo += 1
            if lo == na == nb:
                return
            ha, hb = na, nb
            while ha > lo and hb > lo and equal(a[ha - 1], b[hb - 1]):
                ha -= 1
                hb -= 1
            shared = min(ha - lo, hb - lo)
            for i in range(lo, lo + shared):
                path.append(i)
                walk(a[i], b[i])
                path.pop()
            for i in range(ha - 1, lo + shared - 1, -1):
                path.append(i)
                emit("remove")
                path.pop()
            for i in range(lo + shared, hb):
                path.append(i)
                emit("add", b[i])
                path.pop()
            fallback(start, b)
            return
        if not equal(a, b):
            emit("replace", b)

    walk(old, new)
    return ops
