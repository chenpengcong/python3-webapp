#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__auth__ = 'pcer'

'url handlers'

import re, time, json, logging, hashlib, base64, asyncio

import markdown2
from coroweb import get, post
from aiohttp import web
from models import User, Comment, Blog, next_id

from config import configs
from apis import Page, APIError, APIValueError, APIResourceNotFoundError, APIPermissionError

COOKIE_NAME = 'awesession'
_COOKIE_KEY = configs.session.secret


def check_admin(request):
    '''
    检查该用户是否是管理员，对应user表中的admin字段
    '''
    if request.__user__ is None or not request.__user__.admin:
        raise APIPermissionError()


def get_page_index(page_str):
    '''
    将str类型的页码转为int.
    '''
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p

def user2cookie(user, max_age):
    '''
    Generate cookie str by user.
    '''
    # build cookie string by: id-expires-sha1
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.passwd, expires, _COOKIE_KEY)
    L = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(L)


def text2html(text):
    '''
    将文本转成html，由于'<', '>'等字符在html中会被当成标签，因此要先转换成字符实体，才能在html中 正常显示
    '''
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)


async def cookie2user(cookie_str):
    '''
    Parse cookie and load user if cookie is valid.
    '''
    if not cookie_str:
        return None
    try:
        L = cookie_str.split('-')
        if len(L) != 3:
            return None
        uid, expires, sha1 = L
        if int(expires) < time.time():
            return None
        user = await User.find(uid)
        if user is None:
            return None
        s = '%s-%s-%s-%s' % (uid, user.passwd, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(s.encode('utf-8')).hexdigest():
            logging.info('invalid sha1')
            return None
        user.passwd = '******'
        return user
    except Exception as e:
        logging.exception(e)
        return None


@get('/')
async def index(*, page = '1'):
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')
    page = Page(num, page_index)
    if num == 0:
        blogs = []
    else:
        blogs = await Blog.findAll(orderBy = 'created_at desc', limit = (page.offset, page.limit))
    return {
        '__template__' : 'blogs.html',
        'page' : page,
        'blogs' : blogs
    }


@get('/blog/{id}')
async def get_blog(id):
    '''
    返回blog页面及blog数据.
    '''
    blog = await Blog.find(id)
    comments = await Comment.findAll('blog_id=?', [id], orderby='created_at desc')
    for c in comments:
        c.html_content = text2html(c.content)
    blog.html_content = markdown2.markdown(blog.content)
    return {
        '__template__' : 'blog.html',
        'blog' : blog,
        'comments' : comments
    }



@get('/register')
def register():
    return {
        '__template__' : 'register.html'
    }

@get('/signin')
def signin():
    return {
        '__template__' : 'signin.html'
    }


@get('/signout')
def signout(request):
    # referer是HTTP表头的一个字段，用来表示从哪儿链接到目前的网页，采用的格式是URL
    referer = request.headers.get('Referer')
    r = web.HTTPFound(referer or '/')
    # 把cookie的值修改，使得cookie中不再含有user信息
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age = 0, httponly = True)
    logging.info('user signed out.')
    return r


@get('/manage/')
def manage():
    return 'redirect:/manage/comments'

@get('/manage/comments')
def manage_comments(*, page = '1'):
    '''
    评论管理界面.
    '''
    return {
        '__template__' : 'manage_comments.html',
        'page_index' : get_page_index(page)
    }


@get('/manage/blogs')
def manage_blogs(*, page = '1'):
    '''
    博客管理界面.
    '''
    return {
        '__template__' : 'manage_blogs.html',
        'page_index' : get_page_index(page)
    }



@get('/manage/blogs/create')
def manage_create_blog():
    '''
    写新博客.
    '''
    return {
        '__template__' : 'manage_blog_edit.html',
        'id' : '',
        'action' : '/api/blogs'
    }

@get('/manage/blogs/edit')
def manage_edit_blog(*, id):
    '''
    编辑博客
    '''
    return {
        '__template__' : 'manage_blog_edit.html',
        'id' : id,
        'action' : '/api/blogs/%s' % id
    }


@get('/manage/users')
def manage_users(*, page = '1'):
    '''
    用户管理.
    '''
    return {
        '__template__' : 'manage_users.html',
        'page_index' : get_page_index(page)
    }


'''
RESTFUL API  处理返回数据而不返回html页面
'''


@post('/api/authenticate')
async def authenticate(*, email, passwd):
    '''
    登录时验证密码，并设置cookie, 在signin时进行验证.
    '''
    if not email:
        raise APIValueError('email', 'Invalid email')
    if not passwd:
        raise APIValueError('passwd', 'Invalid passwd.')
    users = await User.findAll('email=?', [email])
    if len(users) == 0:
        raise APIValueError('email', 'Email not exist.')
    user = users[0]
    # check passwd:
    # sha1: create a SHA1 hash object
    sha1 = hashlib.sha1()
    # update: Update the hash object with the object arg,Repeated calls are equivalent to a single call with the concatenation of all the arguments
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(passwd.encode('utf-8'))
    if user.passwd != sha1.hexdigest():
        raise APIValueError('passwd', 'Invalid password.')
    # authenticate ok, set cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age = 86400, httponly = True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii = False).encode('utf-8')
    return r



@get('/api/comments')
async def api_comments(*, page = '1'):
    '''
    获得所有评论, 请求源自manage_comments.html.
    '''
    page_index = get_page_index(page)
    num = await Comment.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page = p, comments = ())
    comments = await Comment.findAll(orderBy = 'created_at desc', limit = (p.offset, p.limit))
    return dict(page = p, comments = comments)


@post('/api/blogs/{id}/comments')
async def api_create_comment(id, request, *, content):
    '''
    提交评论, 请求源自blog.html.
    '''
    user = request.__user__
    if user is None:
        raise APIPermissionError('Please signin first.')
    if not content or not content.strip():
        raise APIValueError('content')
    blog = await Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    comment = Comment(blog_id = blog.id, user_id = user.id, user_name = user.name, user_image = user.image, content = content.strip())
    await comment.save()
    return comment


@post('/api/comments/{id}/delete')
async def api_delete_comments(id, request):
    '''
    删除指定评论.
    '''
    check_admin(request)
    c = await Comment.find(id)
    if c is None:
        raise APIResourceNotFoundError('Comment')
    await c.remove()
    return dict(id = id)

@get('/api/users')
async def api_get_users(*, page = '1'):
    '''
    获得某页的用户信息.
    '''
    page_index = get_page_index(page)
    num = await User.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page = p, users = ())
    users = await User.findAll(orderBy = 'created_at desc', limit = (p.offset, p.limit))
    for u in users:
        u.passwd = '******'
    return dict(page = p, users = users)


_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')


@post('/api/users')
async def api_register_user(*, email, name, passwd):
    '''
    用户注册,并设置相应cookie
    '''
    if not name or not name.strip():
        raise APIValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIValueError('email')
    if not passwd or not _RE_SHA1.match(passwd):
        raise APIValueError('passwd')
    users = await User.findAll('email=?', [email])
    if len(users) > 0:
        raise APIError('register:failed', 'email', 'Email is already in use.') 
    uid = next_id()
    sha1_passwd = '%s:%s' % (uid, passwd)
    # image 使用全球公认头像gravatar，和邮箱绑定
    user = User(id = uid, name = name.strip(), email = email, passwd = hashlib.sha1(sha1_passwd.encode('utf-8')).hexdigest(), image = 'http://www.gravatar.com/avatar/%s?d=mm&s=120' % hashlib.md5(email.encode('utf-8')).hexdigest())
    await user.save()
    # make session cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age = 86400, httponly = True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii = False).encode('utf-8')
    return r

@get('/api/blogs')
async def api_blogs(*, page = '1'):
    '''
    获得所有博客信息, 请求源自manage_blogs.html.
    '''
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page = p, blogs = ())
    blogs = await Blog.findAll(orderby = 'created_at desc', limit = (p.offset, p.limit))
    return dict(page = p, blogs = blogs)


@get('/api/blogs/{id}')
async def api_get_blog(*, id):
    '''
    获得单篇博客信息, 请求源自manage_blog_edit.html.
    '''
    blog = await Blog.find(id)
    return blog

@post('/api/blogs')
async def api_create_blog(request, *, name, summary, content):
    '''
    提交博客日志.
    '''
    #check_admin(request)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog = Blog(user_id = request.__user__.id, user_name = request.__user__.name, user_image = request.__user__.image, name = name.strip(), summary = summary.strip(), content = content.strip())
    await blog.save()
    return blog


@post('/api/blogs/{id}')
async def api_update_blog(id, request, *, name, summary, content):
    '''
    更新指定博客内容
    '''
    check_admin(request)
    blog = await Blog.find(id)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.') 
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog.name = name.strip()
    blog.summary = summary.strip()
    blog.content = content.strip()
    await blog.update()
    return blog

@post('/api/blogs/{id}/delete')
async def api_delete_blog(request, *, id):
    '''
    删除指定博客数据.
    '''
    check_admin(request)
    blog = await Blog.find(id)
    await blog.remove()
    return dict(id = id)