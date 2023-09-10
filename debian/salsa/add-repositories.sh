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

# Add repository details to an apt configuration in the indicated directory.
#
# Usage: add_repositories <apt base directory>

add_repositories()
{
    APT_SOURCES="$1/sources.list.d"
    APT_LIST="${APT_SOURCES}/new-packages-testing.list"
    APT_GPG_TRUSTED_DIR="$1/trusted.gpg.d"

    # Create the configuration resources.

    if [ ! -e "$APT_SOURCES" ] ; then
        mkdir -p "$APT_SOURCES"
    fi

    echo -n > "$APT_LIST"

    if [ ! -e "$APT_GPG_TRUSTED_DIR" ] ; then
        mkdir -p "$APT_GPG_TRUSTED_DIR"
    fi

    # Add configuration entries and public keys for dependencies.

    for PACKAGE in $SALSA_PACKAGES_LIST ; do

        # Define the repository location.

        URL=$(repository_location "$PACKAGE")

        # Generate an obvious failure if there is no appropriate repository.
        # That will hopefully make troubleshooting easier when other jobs try
        # and fetch from this non-existent repository.

        if [ ! "$?" ] ; then
            echo "FAIL! $PACKAGE was not built successfully." >> "$APT_LIST"
        else
            echo "deb ${URL} unstable main" >> "$APT_LIST"

            # Obtain the public key for the repository. Keep redirection
            # support in place in case the server is doing something clever.

            curl -s -L -o "${APT_GPG_TRUSTED_DIR}/${PACKAGE}.asc" "${URL}/${PUBKEY_URL_SUFFIX}"
        fi
    done
}

# Download packages into the current directory.
#
# Usage: get_packages

get_packages()
{
    # Add configuration entries and public keys for dependencies.

    for PACKAGE in $SALSA_PACKAGES_LIST ; do

        # Download source package files.

        apt-get --download-only --only-source source "$PACKAGE"

        # Obtain the binary package name and attempt to download the package.

        BINARIES=$(grep -e '^Binary:' "$PACKAGE"_*.dsc | cut -d: -f2 | sed 's/,//')

        for BINARY in $BINARIES ; do
            apt-get download "$BINARY"
        done
    done
}

# Provide different behaviours depending on how this file is invoked.

# Using a list of packages, define a configuration for apt that allows the
# listed packages to be obtained for installation.
#
# Usage: add-repositories.sh <apt base directory>

if [ $(basename "$0") = 'add-repositories.sh' ] ; then

    APT_PATH=${1:-"/etc/apt"}

    # First, make sure that curl is available.

    apt-get update
    NON_INTERACTIVE=1 apt-get install -y curl

    # Obtain the package and repository details.

    add_repositories "$APT_PATH"

    # Update the repository records.

    apt-get update
fi

# Using a list of packages, download source and binary packages using apt-get
# into the indicated directory.
#
# Usage: get-packages.sh <directory>

if [ $(basename "$0") = 'get-packages.sh' ] ; then

    PACKAGE_DIR=${1:-.}

    # Enter the directory, creating it if necessary.

    if [ ! -e "$PACKAGE_DIR" ] ; then
        mkdir -p "$PACKAGE_DIR"
    fi

    cd "$PACKAGE_DIR"

    # Obtain packages.

    get_packages
fi
