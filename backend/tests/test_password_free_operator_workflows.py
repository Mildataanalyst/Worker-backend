import main


def test_avika_shortlisting_workflow_paths_are_password_free():
    assert main._public_operator_mutation_path('/admin/ngo-ids/backfill') is True
    assert main._public_operator_mutation_path('/workspace/Karnataka/lead-pool/import') is True
    assert main._public_operator_mutation_path('/workspace/Karnataka/lead-pool/curate') is True
    assert main._public_operator_mutation_path('/workspace/Karnataka/lead-pool/delete') is True
    assert main._public_operator_mutation_path('/workspace/Karnataka/send-to-ranking') is True
    # Unrelated admin and final-output mutations stay protected.
    assert main._public_operator_mutation_path('/ranking/final/send-to-contact-tracker') is False
    assert main._public_operator_mutation_path('/repository/runs/delete') is False
