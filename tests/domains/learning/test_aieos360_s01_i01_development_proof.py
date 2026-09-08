"""AIEOS360-S01-I01 — executable development membership proof.

No HTTP. No ERP. Synthetic development tenant + Student A only.
"""

from __future__ import annotations

import pytest

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)
from aieos.development.learner_school_context import (
    development_learner_membership_authority,
)
from aieos.domains.learning.application.errors import LearnerClassMembershipDenied

pytestmark = pytest.mark.aieos360_s01_i01


def test_student_a_class_5a_pass_and_class_5b_deny() -> None:
    authority = development_learner_membership_authority(
        tenant_id=SYNTHETIC_TENANT_ID
    )
    item = authority.require_current_membership(
        SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, CLASS_REF_5A
    )
    assert item.class_ref == CLASS_REF_5A

    with pytest.raises(LearnerClassMembershipDenied):
        authority.require_current_membership(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, CLASS_REF_5B
        )
