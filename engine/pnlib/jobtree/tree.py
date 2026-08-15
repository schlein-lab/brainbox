

from collections import defaultdict, deque

class Node:

    __slots__ = ("id", "parent_job", "state", "row", "children")

    def __init__(self, row):
        self.id = row["id"]
        self.parent_job = row["parent_job"]
        self.state = row["state"]
        self.row = row
        self.children = []

    def __repr__(self):
        return f"<Node #{self.id} state={self.state!r} parent={self.parent_job} kids={len(self.children)}>"

class JobTree:

    def __init__(self, rows):

        self.nodes = {}
        self._children = defaultdict(list)
        for row in sorted(rows, key=lambda r: r["id"]):
            self.nodes[row["id"]] = Node(row)

        self.roots = []
        for jid, node in sorted(self.nodes.items()):
            p = node.parent_job
            if p is not None and p in self.nodes and p != jid:
                self.nodes[p].children.append(node)
                self._children[p].append(jid)
            else:

                self.roots.append(node)

    def get(self, job_id):
        return self.nodes.get(job_id)

    def children_ids(self, job_id):

        return list(self._children.get(job_id, ()))

    def descendants(self, job_id):

        out = []
        seen = {job_id}
        stack = list(reversed(self._children.get(job_id, ())))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out.append(cur)

            stack.extend(reversed(self._children.get(cur, ())))
        return out

    def subtree_ids(self, job_id):

        if job_id not in self.nodes:
            return []
        return [job_id] + self.descendants(job_id)

    def ancestors(self, job_id):

        out = []
        seen = {job_id}
        node = self.nodes.get(job_id)
        while node is not None and node.parent_job is not None:
            p = node.parent_job
            if p in seen or p not in self.nodes:
                break
            seen.add(p)
            out.append(p)
            node = self.nodes[p]
        return out

    def root_of(self, job_id):

        chain = self.ancestors(job_id)
        return chain[-1] if chain else job_id

    def walk(self, job_id):

        for jid in self.subtree_ids(job_id):
            yield self.nodes[jid]

    def __len__(self):
        return len(self.nodes)

def build_tree(cx, table: str = "jobs", where: str = None, params=()) -> JobTree:

    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    rows = cx.execute(sql, params).fetchall()
    return JobTree(rows)

def subtree_ids(cx, job_id, table: str = "jobs") -> list:

    return build_tree(cx, table).subtree_ids(job_id)
