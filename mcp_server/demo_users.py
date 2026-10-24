"""Demo users for the four roles, and a helper that prints a signed token for each.

The API (week 2) signs these in; until then, paste a printed token into the MCP inspector.

    uv run python -m mcp_server.demo_users            # all users
    uv run python -m mcp_server.demo_users u_bm_tahlia
"""

import sys

from dotenv import load_dotenv

from mcp_server.scope import ALL_BRANCHES, Scope, issue_token

DEMO_USERS = {
    "u_owner": ("Reem Al-Saud (owner)", Scope("u_owner", "owner", ALL_BRANCHES)),
    "u_area_riyadh": ("Khalid Al-Harbi (Riyadh area manager)",
                      Scope("u_area_riyadh", "area_manager", ("B1", "B4"))),
    "u_bm_olaya": ("Sara Al-Qahtani (Olaya branch manager)", Scope("u_bm_olaya", "branch_manager", ("B1",))),
    "u_bm_tahlia": ("Omar Al-Ghamdi (Tahlia branch manager)", Scope("u_bm_tahlia", "branch_manager", ("B2",))),
    "u_bm_smouha": ("Mariam Fahmy (Smouha branch manager)", Scope("u_bm_smouha", "branch_manager", ("B3",))),
    "u_bm_malqa": ("Turki Al-Dosari (Al Malqa branch manager)", Scope("u_bm_malqa", "branch_manager", ("B4",))),
    "u_staff_olaya": ("Olaya barista (staff)", Scope("u_staff_olaya", "staff", ("B1",), staff_id="B1-S09")),
}


def main() -> None:
    load_dotenv()
    wanted = sys.argv[1:] or list(DEMO_USERS)
    for user_id in wanted:
        label, scope = DEMO_USERS[user_id]
        print(f"# {user_id}: {label}\n{issue_token(scope)}\n")


if __name__ == "__main__":
    main()
