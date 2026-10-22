# Security policy

## Supported versions

This is BETA software. Only the latest release (currently 0.9.0-beta.1) is looked at.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**.
Do not put exploit details in a public issue.

What counts: the brain is a Lua mod and the tools are local Python scripts that read files from your own install. Realistic problems are a
tool that writes outside its own folder, a script that executes content from a character or roster file, or a leaked path or credential in the repository.
