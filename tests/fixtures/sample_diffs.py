"""Sample diff strings used in integration tests.

These represent realistic GitHub PR diffs for different scenarios.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
#  Simple Python PR — 3-file change, no security issues
# ---------------------------------------------------------------------------

SIMPLE_PYTHON_DIFF = """\
diff --git a/app/utils.py b/app/utils.py
--- a/app/utils.py
+++ b/app/utils.py
@@ -10,6 +10,10 @@ def get_user(user_id):
     return db.query(User).filter(User.id == user_id).first()


+def calculate_discount(price: float, percentage: float) -> float:
+    \"\"\"Calculate discounted price.\"\"\"
+    return price * (1 - percentage / 100)
+
 def format_date(date):
     return date.strftime("%Y-%m-%d")
diff --git a/tests/test_utils.py b/tests/test_utils.py
--- a/tests/test_utils.py
+++ b/tests/test_utils.py
@@ -1,5 +1,12 @@
 import pytest
 from app.utils import get_user, format_date
+from app.utils import calculate_discount
+
+
+def test_calculate_discount():
+    assert calculate_discount(100.0, 10.0) == 90.0
+    assert calculate_discount(50.0, 50.0) == 25.0
+    assert calculate_discount(0.0, 100.0) == 0.0


 def test_format_date():
diff --git a/app/models.py b/app/models.py
--- a/app/models.py
+++ b/app/models.py
@@ -5,4 +5,5 @@ class User(Base):
     id = Column(Integer, primary_key=True)
     name = Column(String(255))
     email = Column(String(255))
+    discount_tier = Column(String(50), default="standard")
"""

# ---------------------------------------------------------------------------
#  Security PR — SQL injection vulnerability
# ---------------------------------------------------------------------------

SECURITY_PYTHON_DIFF = """\
diff --git a/app/db/queries.py b/app/db/queries.py
--- a/app/db/queries.py
+++ b/app/db/queries.py
@@ -1,8 +1,15 @@
 from app.db import get_connection


+def get_user_by_name(username: str):
+    \"\"\"Fetch user by username.\"\"\"
+    conn = get_connection()
+    cursor = conn.cursor()
+    query = f"SELECT * FROM users WHERE username = '{username}'"
+    cursor.execute(query)
+    return cursor.fetchone()
+
+
 def get_all_users():
     conn = get_connection()
     cursor = conn.cursor()
     cursor.execute("SELECT * FROM users")
     return cursor.fetchall()
"""

# ---------------------------------------------------------------------------
#  Bot PR — dependabot dependency update (should be filtered)
# ---------------------------------------------------------------------------

BOT_PR_DIFF = """\
diff --git a/requirements.txt b/requirements.txt
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,3 +1,3 @@
 fastapi==0.100.0
-httpx==0.24.0
+httpx==0.27.0
 pytest==7.4.0
"""

# ---------------------------------------------------------------------------
#  TypeScript React component PR
# ---------------------------------------------------------------------------

TYPESCRIPT_DIFF = """\
diff --git a/src/components/Button.tsx b/src/components/Button.tsx
--- /dev/null
+++ b/src/components/Button.tsx
@@ -0,0 +1,24 @@
+import React from 'react';
+
+interface ButtonProps {
+  label: string;
+  onClick: () => void;
+  disabled?: boolean;
+  variant?: 'primary' | 'secondary';
+}
+
+const Button: React.FC<ButtonProps> = ({
+  label,
+  onClick,
+  disabled = false,
+  variant = 'primary',
+}) => {
+  return (
+    <button
+      className={`btn btn-${variant}`}
+      onClick={onClick}
+      disabled={disabled}
+    >
+      {label}
+    </button>
+  );
+};
+
+export default Button;
"""

# ---------------------------------------------------------------------------
#  Webhook payloads
# ---------------------------------------------------------------------------

SIMPLE_PR_WEBHOOK = {
    "action": "opened",
    "installation": {"id": 12345},
    "repository": {
        "id": 99999,
        "full_name": "testorg/testrepo",
    },
    "pull_request": {
        "number": 42,
        "title": "Add calculate_discount utility function",
        "body": "Adds a utility function for calculating discounts.",
        "head": {"sha": "abc123def456"},
        "base": {"sha": "base000sha000"},
        "user": {"login": "developer"},
        "draft": False,
        "labels": [],
    },
    "sender": {"login": "developer"},
}

SECURITY_PR_WEBHOOK = {
    "action": "opened",
    "installation": {"id": 12345},
    "repository": {
        "id": 99999,
        "full_name": "testorg/testrepo",
    },
    "pull_request": {
        "number": 43,
        "title": "Add user lookup by username",
        "body": "Adds a function to look up users by username.",
        "head": {"sha": "secabc123def"},
        "base": {"sha": "base000sha000"},
        "user": {"login": "developer"},
        "draft": False,
        "labels": [],
    },
    "sender": {"login": "developer"},
}

BOT_PR_WEBHOOK = {
    "action": "opened",
    "installation": {"id": 12345},
    "repository": {
        "id": 99999,
        "full_name": "testorg/testrepo",
    },
    "pull_request": {
        "number": 44,
        "title": "Bump httpx from 0.24.0 to 0.27.0",
        "body": "Bumps httpx from 0.24.0 to 0.27.0.",
        "head": {"sha": "botabc123def"},
        "base": {"sha": "base000sha000"},
        "user": {"login": "dependabot[bot]"},
        "draft": False,
        "labels": [],
    },
    "sender": {"login": "dependabot[bot]"},
}

TYPESCRIPT_PR_WEBHOOK = {
    "action": "opened",
    "installation": {"id": 12345},
    "repository": {
        "id": 99999,
        "full_name": "testorg/frontend-repo",
    },
    "pull_request": {
        "number": 45,
        "title": "Add Button component",
        "body": "Adds a reusable Button React component with TypeScript.",
        "head": {"sha": "tsabc123def4"},
        "base": {"sha": "base000sha000"},
        "user": {"login": "frontend-dev"},
        "draft": False,
        "labels": [],
    },
    "sender": {"login": "frontend-dev"},
}
