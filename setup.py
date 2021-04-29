import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    author='Kamil Mierzejewski',
    name='django-native-search',
    version='0.5.4',
    description='A simple search engine using native django database backend.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/kmierzeje/django-native-search',
    packages=setuptools.find_packages(),
    install_requires=['django>=3.0.8', 'django-expression-index>=0.1.0'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Framework :: Django',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3.7',
    ],
)
