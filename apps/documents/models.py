import errno
import os
import mimetypes
from datetime import datetime
import sys

from django.conf import settings
from django.db import models
from django.template.defaultfilters import slugify
from django.utils.translation import ugettext_lazy as _
from django.utils.translation import ugettext
 
from dynamic_search.api import register

from documents.conf.settings import AVAILABLE_FUNCTIONS
from documents.conf.settings import AVAILABLE_MODELS
from documents.conf.settings import CHECKSUM_FUNCTION
from documents.conf.settings import UUID_FUNCTION
from documents.conf.settings import STORAGE_BACKEND
from documents.conf.settings import STORAGE_DIRECTORY_NAME
from documents.conf.settings import FILESYSTEM_FILESERVING_ENABLE
from documents.conf.settings import FILESYSTEM_FILESERVING_PATH
from documents.conf.settings import FILESYSTEM_SLUGIFY_PATHS


if FILESYSTEM_SLUGIFY_PATHS == False:
    #Do not slugify path or filenames and extensions
    slugify = lambda x:x


def get_filename_from_uuid(instance, filename, directory=STORAGE_DIRECTORY_NAME):
    populate_file_extension_and_mimetype(instance, filename)
    return '%s/%s' % (directory, instance.uuid)

def populate_file_extension_and_mimetype(instance, filename):
    # First populate the file extension and mimetype
    instance.file_mimetype, encoding = mimetypes.guess_type(filename)
    if not instance.file_mimetype:
         instance.file_mimetype = u'unknown'
    filename, extension = os.path.splitext(filename)
    instance.file_filename = filename
    #remove prefix '.'
    instance.file_extension = extension[1:]
    

def custom_eval(format, dictionary):
    try:
        #Do a normal substitution
        return format % dictionary
    except:
        #Use exception to catch unknown elements
        (exc_type, exc_info, tb) = sys.exc_info()
        key = unicode(exc_info)[2:-1]
        try:
            #Resolve unknown element
            dictionary[key] = eval(key, dictionary)
            #Call itself again, but with an additional resolved element in
            #the dictionary
            return custom_eval(format, dictionary)
        except Exception, e:
            #Can't resolve elemtent, give up
            (exc_type, exc_info, tb) = sys.exc_info()
            print exc_info
            raise Exception(e)    
            

class DocumentType(models.Model):
    name = models.CharField(max_length=32, verbose_name=_(u'name'))    
    
    def __unicode__(self):
        return self.name


class Document(models.Model):
    """ Minimum fields for a document entry.
        Inherit this model to customise document metadata, see BasicDocument for an example.
    """
    document_type = models.ForeignKey(DocumentType, verbose_name=_(u'document type'))
    file = models.FileField(upload_to=get_filename_from_uuid, storage=STORAGE_BACKEND(), verbose_name=_(u'file'))
    uuid = models.CharField(max_length=48, default=UUID_FUNCTION(), blank=True, editable=False)
    file_mimetype = models.CharField(max_length=64, default='', editable=False)
    #FAT filename can be up to 255 using LFN
    file_filename = models.CharField(max_length=64, default='', editable=False)
    file_extension = models.CharField(max_length=16, default='', editable=False)
    date_added = models.DateTimeField(verbose_name=_(u'added'), auto_now_add=True)
    date_updated = models.DateTimeField(verbose_name=_(u'updated'), auto_now=True)
    checksum = models.TextField(blank=True, null=True, verbose_name=_(u'checksum'), editable=False)
    
    class Meta:
        verbose_name = _(u'document')
        verbose_name_plural = _(u'documents')
        ordering = ['-date_updated', '-date_added']
        
    def __unicode__(self):
        #return self.uuid
        return '%s.%s' % (self.file_filename, self.file_extension)
        
    @models.permalink
    def get_absolute_url(self):
        return ('document_view', [self.id])

    def update_checksum(self, save=True):
        self.checksum = unicode(CHECKSUM_FUNCTION(self.file.read()))
        if save:
            self.save()
    
    def exists(self):
        return self.file.storage.exists(self.file.url)
        
    def save(self, *args, **kwargs):
        self.update_checksum(save=False)
        super(Document, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        #TODO: Might not execute when done in bulk from a queryset
        #topics/db/queries.html#topics-db-queries-delete
        self.delete_fs_links()
        super(Document, self).delete(*args, **kwargs)

    def calculate_fs_links(self):
        metadata_dict = {'document':self}
        metadata_dict.update(dict([(metadata.metadata_type.name, slugify(metadata.value)) for metadata in self.documentmetadata_set.all()]))
            
        for metadata_index in self.document_type.metadataindex_set.all():
            if metadata_index.enabled:
            #print eval(metadata_index.expression, metadata_dict)
                fabricated_directory = custom_eval(metadata_index.expression, metadata_dict)
                target_directory = os.path.join(FILESYSTEM_FILESERVING_PATH, fabricated_directory)
                #print target_directory
                
                
                final_path = os.path.join(target_directory, os.extsep.join([slugify(self.file_filename), slugify(self.file_extension)]))
                print final_path
        #targets = []
        #for metadata in self.documentmetadata_set.all():
        #    if metadata.metadata_type.documenttypemetadatatype_set.all()[0].create_directory_link:
        #        target_directory = os.path.join(FILESYSTEM_FILESERVING_PATH, slugify(metadata.metadata_type.name), slugify(metadata.value))
        #        targets.append(os.path.join(target_directory, os.extsep.join([slugify(self.file_filename), slugify(self.file_extension)])))
        #return targets

    def create_fs_links(self):
        if FILESYSTEM_FILESERVING_ENABLE:
            for target in self.calculate_fs_links():
                try:
                    os.makedirs(os.path.dirname(target))
                except OSError, exc:
                    if exc.errno == errno.EEXIST:
                        pass
                    else: 
                        raise OSError(ugettext(u'Unable to create metadata indexing directory: %s') % exc)
                try:
                    os.symlink(os.path.abspath(self.file.path), target)
                except OSError, exc:
                    if exc.errno == errno.EEXIST:
                        pass
                    else: 
                        raise OSError(ugettext(u'Unable to create metadata indexing symbolic link: %s') % exc)
                    
    def delete_fs_links(self):
        if FILESYSTEM_FILESERVING_ENABLE:
            for target in self.calculate_fs_links():
                try:
                    os.unlink(target)
                except OSError, exc:
                    if exc.errno == errno.ENOENT:
                        pass
                    else: 
                        raise OSError(ugettext(u'Unable to delete metadata indexing symbolic link: %s') % exc)
 
       

available_functions_string = (_(u' Available functions: %s') % ','.join(['%s()' % name for name, function in AVAILABLE_FUNCTIONS.items()])) if AVAILABLE_FUNCTIONS else ''
available_models_string = (_(u' Available models: %s') % ','.join([name for name, model in AVAILABLE_MODELS.items()])) if AVAILABLE_MODELS else ''

class MetadataType(models.Model):
    name = models.CharField(max_length=48, verbose_name=_(u'name'))
    title = models.CharField(max_length=48, verbose_name=_(u'title'), blank=True, null=True)
    default = models.CharField(max_length=128, blank=True, null=True,
        verbose_name=_(u'default'),
        help_text=_(u'Enter a string to be evaluated.%s') % available_functions_string)
    lookup = models.CharField(max_length=128, blank=True, null=True,
        verbose_name=_(u'lookup'),
        help_text=_(u'Enter a string to be evaluated.  Example: [user.get_full_name() for user in User.objects.all()].%s') % available_models_string)
    #TODO: datatype?
    
    def __unicode__(self):
        #return '%s - %s' % (self.name, self.title if self.title else self.name)
        return self.name
        
    class Meta:
        verbose_name = _(u'metadata type')
        verbose_name_plural = _(u'metadata types')


class DocumentTypeMetadataType(models.Model):
    document_type = models.ForeignKey(DocumentType, verbose_name=_(u'document type'))
    metadata_type = models.ForeignKey(MetadataType, verbose_name=_(u'metadata type'))
    #create_directory_link = models.BooleanField(verbose_name=_(u'create directory link'))
    #TODO: override default for this document type
    #TODO: required? -bool
    
    def __unicode__(self):
        return unicode(self.metadata_type)

    class Meta:
        verbose_name = _(u'document type metadata type connector')
        verbose_name_plural = _(u'document type metadata type connectors')


class MetadataIndex(models.Model):
    document_type = models.ForeignKey(DocumentType, verbose_name=_(u'document type'))
    expression = models.CharField(max_length=128,
        verbose_name=_(u'indexing expression'),
        help_text=_(u'Enter a python string expression to be evaluated.  The slash caracter "/" acts as a directory delimiter.'))
    enabled = models.BooleanField(default=True, verbose_name=_(u'enabled'))
    
    def __unicode__(self):
        return unicode(self.expression)
        
    class Meta:
        verbose_name = _(u'metadata index')
        verbose_name_plural = _(u'metadata indexes')


class DocumentMetadataIndex(models.Model):
    document = models.ForeignKey(Document, verbose_name=_(u'document'))
    metadata_indexing = models.ForeignKey(MetadataIndex, verbose_name=_(u'metadata indexing'))
    filename = models.CharField(max_length=128)

    def __unicode__(self):
        return unicode(self.filename)

    class Meta:
        verbose_name = _(u'document metadata index')
        verbose_name_plural = _(u'document metadata indexes')


class DocumentMetadata(models.Model):
    document = models.ForeignKey(Document, verbose_name=_(u'document'))
    metadata_type = models.ForeignKey(MetadataType, verbose_name=_(u'metadata type'))
    value = models.TextField(blank=True, null=True, verbose_name=_(u'metadata value'))
 
    def __unicode__(self):
        return unicode(self.metadata_type)

    class Meta:
        verbose_name = _(u'document metadata')
        verbose_name_plural = _(u'document metadata')


class DocumentTypeFilename(models.Model):
    document_type = models.ForeignKey(DocumentType, verbose_name=_(u'document type'))
    filename = models.CharField(max_length=64, verbose_name=_(u'filename'))
    enabled = models.BooleanField(default=True, verbose_name=_(u'enabled'))
    
    def __unicode__(self):
        return self.filename

    class Meta:
        ordering = ['filename']
        verbose_name = _(u'document type filename')
        verbose_name_plural = _(u'document types filenames')
        

register(Document, _(u'document'), ['document_type__name', 'file_mimetype', 'file_filename', 'file_extension', 'documentmetadata__value'])
