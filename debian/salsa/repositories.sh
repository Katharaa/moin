#!/bin/sh

# The packages list would preferably be separate, but this requires extra effort
# when setting up various environments.

SALSA_PACKAGES_LIST='
emeraldtree
feedgen
flask-theme
flatland
python-xstatic-autosize
python-xstatic-bootstrap
python-xstatic-ckeditor
python-xstatic-jquery-file-upload
python-xstatic-pygments
'

# Define repository location details for package dependencies.

TEAM_URL="https://salsa.debian.org/moin-team"
BRANCH="debian/master"
APTLY_URL_SUFFIX="-/jobs/artifacts/${BRANCH}/raw/aptly?job=aptly"
PUBKEY_URL_SUFFIX="public-key.asc"

# Return the repository location for a given package.
#
# Usage: repository_location <package>

repository_location()
{
    # Handle redirects by obtaining the last URL fetched. This eliminates
    # the need to enable redirects in apt.

    START_URL="${TEAM_URL}/${PACKAGE}/${APTLY_URL_SUFFIX}"

    # Let curl output the location itself.

    curl -s -L -I -w '%{url_effective}' -o /dev/null "$START_URL"

    # Return success or failure.

    if [ "$URL" = "$START_URL" ] ; then
        return 1
    else
        return 0
    fi
}
