from advisorai.collectors import AcquisitionPolicy, AcquisitionStep


def test_acquisition_ladder_requires_compliance_and_lower_step_failure():
    policy = AcquisitionPolicy()
    denied = policy.decide(
        source="public",
        step=AcquisitionStep.PLAYWRIGHT,
        public=True,
        robots_allowed=True,
        rate_limit_allowed=True,
        authentication_required=False,
        active_content_quarantined=True,
        lower_steps_failed=False,
    )
    assert not denied.admitted
    admitted = policy.decide(
        source="public",
        step=AcquisitionStep.PLAYWRIGHT,
        public=True,
        robots_allowed=True,
        rate_limit_allowed=True,
        authentication_required=False,
        active_content_quarantined=True,
        lower_steps_failed=True,
    )
    assert admitted.admitted
