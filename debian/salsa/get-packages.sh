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

PACKAGE_DIR=${1:-.}

# Enter the directory, creating it if necessary.

if [ ! -e "$PACKAGE_DIR" ] ; then
    mkdir -p "$PACKAGE_DIR"
fi

cd "$PACKAGE_DIR"

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
