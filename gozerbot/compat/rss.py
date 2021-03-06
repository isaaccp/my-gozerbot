# plugs/rss.py
#
#

"""the rss mantra is of the following:

as OPER:

. add a url with rss-add
. start the watcher with rss-watch

now the user can start the bot sending messages of the feed to him with
rss-start. if an OPER gives a rss-start command in a channel data will be
send to the channel.
 """

__copyright__ = 'this file is in the public domain'
__gendocfirst__ = ['rss-add', 'rss-watch', 'rss-start']
__gendocskip__ = ['rss-dump', ]

from gozerbot.compat.persist import Persist
from gozerbot.generic import geturl2, handle_exception, rlog, lockdec, \
strippedtxt, fromenc, striphtml, useragent
from gozerbot.utils.rsslist import rsslist
from gozerbot.utils.statdict import Statdict
from gozerbot.fleet import fleet
from gozerbot.commands import cmnds
from gozerbot.examples import examples
from gozerbot.datadir import datadir
from gozerbot.utils.dol import Dol
from gozerbot.compat.pdod import Pdod
from gozerbot.compat.pdol import Pdol
from gozerbot.plughelp import plughelp
from gozerbot.periodical import periodical
from gozerbot.aliases import aliasset
from gozerbot.users import users
import feedparser
import gozerbot.threads.thr as thr
import time, os, types, thread, socket, xml

plughelp.add('rss', 'manage rss feeds')

savelist = []

def txtindicts(result, d):
    """ return lowlevel values in (nested) dicts """
    for j in d.values():
        if type(j) == types.DictType:
            txtindicts(result, j) 
        else:
            result.append(j)

def checkfordate(data, date):
    for item in data:
        try:
            d = item['updated']
        except KeyError:
            continue
        if date == d:
            return True
    return False

rsslock = thread.allocate_lock()
locked = lockdec(rsslock)

class RssException(Exception):
    pass

class Rss301(RssException):
    pass

class RssStatus(RssException):
    pass

class RssBozoException(RssException):
    pass

class RssNoSuchItem(RssException):
    pass

class Rssitem(object):

    """ item that contains rss data """

    def __init__(self, name, url, itemslist, watchchannels=[], \
sleeptime=30*60):
        self.name = name
        self.url = url
        self.itemslist = list(itemslist)
        self.watchchannels = list(watchchannels)
        self.sleeptime = int(sleeptime)
        self.running = 0
        self.stoprunning = 0
        self.botname = None

    def __str__(self):
        return "name=%s url=%s itemslist=%s watchchannels=%s sleeptime=%s \
running=%s" % (self.name, self.url, str(self.itemslist), \
str(self.watchchannels), str(self.sleeptime), self.running)

class Rssdict(Persist):

    """ dict of rss entries """

    def __init__(self, filename):
        Persist.__init__(self, filename)
        if not self.data:
            self.data = {}
        if self.data.has_key('itemslists'):
            del self.data['itemslists']
        self.itemslists = Pdol(filename + '.itemslists')
        self.handlers = {}
        self.results = {}
        self.jobids = {}
        self.rawresults = {}
        self.results = Dol()
        self.modified = {}
        self.etag = {}
        self.markup = Pdod(filename + '.markup')

    def size(self):
        """ return number of rss entries """
        return len(self.data)

    @locked
    def add(self, name, url):
        """ add rss item """
        rlog(10, 'rss', 'adding %s %s' % (name, url))
        self.data[name] = Rssitem(name, url, ['title', ])
        self.save()

    @locked
    def delete(self, name):
        """ delete rss item by name """
        target = None
        for j, i in self.data.iteritems():
           if i.name == name:
               target = i
        if target:
            try:
                target.running = 0
                del self.data[name]
                self.save()
            except:
                pass

    def byname(self, name):
        """ return rss item by name """
        try:
            return self.data[name]
        except:
            return

    def getdata(self, name):
        """ get data of rss feed """
        rssitem = self.byname(name)
        if rssitem == None:
            raise RssNoSuchItem("no %s rss item found" % name)
        try:
            modified = self.modified[name]
        except KeyError:
            modified = None
        try:
            etag = self.etag[name]
        except KeyError:
            etag = None
        result = feedparser.parse(rssitem.url, modified=modified, etag=etag, \
agent=useragent())
        if result and result.has_key('bozo_exception'):
            rlog(1, 'rss', '%s bozo_exception: %s' % (name, \
result['bozo_exception']))
            #raise RssStatus(result['bozo_exception'])
        try:
            status = result.status
        except AttributeError:
            status = 200
        if status != 200 and status != 301 and status != 302 and status != 304:
            raise RssStatus(status)
        try:
            self.modified[name] = result.modified
        except AttributeError:
            pass
        try:
            self.etag[name] = result.etag
        except AttributeError:
            pass
        if status == 304:
            return self.rawresults[name]
        else:
            self.rawresults[name] = result.entries
        return result.entries

class Rsswatcher(Rssdict):

    """ rss watchers """ 

    def __init__(self, filename):
        Rssdict.__init__(self, filename)
        
    def sync(self, name):
        result = self.getdata(name)
        if result:
            self.results[name] = result

    def changeinterval(self, name, interval):
        periodical.changeinterval(self.jobids[name], interval)

    @locked
    def startwatchers(self):
        """ start watcher threads """
        for j, i in self.data.iteritems():
            if i.running:
                thr.start_new_thread(self.watch, (i.name, ))

    @locked
    def stopwatchers(self):
        """ stop all watcher threads """
        for j, i in self.data.iteritems():
            if i.running:
                i.stoprunning = 1
        periodical.killgroup('rss')

    def dowatch(self, name, sleeptime=1800):
        rssitem = self.byname(name)
        if not rssitem:
            rlog(10, 'rss', "no %s rss item available" % name)
            return
        while 1:
            try:
                self.watch(name)
            except Exception, ex:
                rlog(100, 'rss', '%s feed error: %s' % (name, str(ex)))
                rlog(100, 'rss', '%s sleeping %s seconds for retry' % \
(name, sleeptime))
                time.sleep(sleeptime)
                if not rssitem.running:
                    break
            else:
                break

    def makeresult(self, name, target, data):
        res = []
        for j in data:
            tmp = {}
            if not self.itemslists[(name, target)]:
                return []
            for i in self.itemslists[(name, target)]:
                try:
                    tmp[i] = unicode(j[i])
                except KeyError:
                    continue
            res.append(tmp)
        return res

    def watch(self, name):
        """ start a watcher thread """
        # get basic data
        rlog(10, 'rss', 'trying %s rss feed watcher' % name)
        try:
            result = self.getdata(name)
        except RssException, ex:
            rlog(10, 'rss', "%s error: %s" % (name, str(ex)))
            result = []
        rssitem = self.byname(name)
        if not rssitem:
            raise RssNoItem()
        # poll every sleeptime seconds
        self.results[name] = result
        pid = periodical.addjob(rssitem.sleeptime, 0, self.peek, name, name)
        self.jobids[name] = pid
        rlog(10, 'rss', 'started %s rss watch' % name)

    def makeresponse(self, name, res, channel, sep="\002||\002"):
        # loop over result to make a response
        result = ""
        itemslist = self.itemslists[(name, channel)]
        if not itemslist:
            rssitem = self.byname(name)
            if not rssitem:
                return "no %s rss item" % name
            else:
                self.itemslists.extend((name, channel), rssitem.itemslist)
                self.itemslists.save()
        for j in res:
            resultstr = ""
            for i in self.itemslists[(name, channel)]:
                try:
                    item = unicode(j[i])
                    if not item:
                        continue
                    if item.startswith('http://'):
                        resultstr += "<%s> - " % item
                    else:
                        resultstr += "%s - " % striphtml(item)
                except KeyError:
                    continue
            resultstr = resultstr[:-3]
            if resultstr:
                result += "%s %s " % (resultstr, sep)
        return result[:-6]

    def peek(self, name, *args):
        rssitem = self.byname(name)
        if not rssitem or not rssitem.running or rssitem.stoprunning:
            return
        try:
            try:
                res = self.getdata(name)
            except socket.timeout:
                rlog(10, 'rss', 'socket timeout of %s' % name)
                return
            except RssException, ex:
                rlog(10, 'rss', '%s error: %s' % (name, str(ex)))
                return
            if not res:
                return
            res2 = []
            for j in res:
                try:
                    d = j['date']
                except KeyError:
                    if j not in self.results[name]:
                        self.results[name].append(j)
                        res2.append(j)
                else:
                    if not checkfordate(self.results[name], d):
                        self.results[name].append(j)
                        res2.append(j)
            if not res2:
                return
            for item in rssitem.watchchannels:
                try:
                    (botname, channel) = item
                except:
                    rlog(10, 'rss', '%s is not in the format \
(botname,channel)' % str(item))
                bot = fleet.byname(botname)
                if not bot:
                    continue
                if self.markup.get((name, channel), 'all-lines'):
                    for i in res2:
                        response = self.makeresponse(name, [i, ], channel)
                        bot.say(channel, "\002%s\002: %s" % \
(rssitem.name, response), fromm=rssitem.name)
                else:
                    sep =  self.markup.get((name, channel), 'seperator')
                    if sep:
                        response = self.makeresponse(name, res2, channel, \
sep=sep)
                    else:
                        response = self.makeresponse(name, res2, channel)
                    bot.say(channel, "\002%s\002: %s" % (rssitem.name, \
response), fromm=rssitem.name)
        except Exception, ex:
            handle_exception(txt=name)

    @locked
    def stopwatch(self, name):
        """ stop watcher thread """
        for i, j in self.data.iteritems():
            if i == name:
                j.running = 0
                try:
                    del self.results[name]
                except KeyError:
                    pass
                self.save()
                try:
                    periodical.killjob(self.jobids[i])
                except KeyError:
                    pass
                return 1

    @locked
    def list(self):
        """ return of rss names """
        feeds = self.data.keys()
        return feeds

    @locked
    def runners(self):	
        """ show names/channels of running watchers """
        result = []
        for j, i in self.data.iteritems():
            if i.running == 1 and not i.stoprunning: 
                result.append((i.name, i.watchchannels))
        return result

    @locked
    def feeds(self, botname, channel):
        """ show names/channels of running watcher """
        result = []
        for j, i in self.data.iteritems():
            if (botname, channel) in i.watchchannels:
                result.append(i.name)
        return result

    @locked
    def url(self, name):
        """ return url of rssitem """
        for j, i in self.data.iteritems():
            if i.name == name:
                return i.url

    def scan(self, name):
        """ scan a rss url for used xml items """
        try:
            result = self.getdata(name)
        except RssException, ex:
            rlog(10, 'rss', '%s error: %s' % (name, str(ex)))
            return
        if not result:
            return
        keys = []
        for item in self.rawresults[name]:
             for key in item.keys():
                 keys.append(key)            
        statdict = Statdict()
        for key in keys:
            statdict.upitem(key)
        return statdict.top()  

    def search(self, name, item, search):
        res = []
        for result in self.rawresults[name]:
            try:
                title = result['title']
                txt = result[item]
            except KeyError:
                continue
            if search in title.lower():
                if txt:
                   res.append(txt)
        return res

    def searchall(self, item, search):
        res = []
        for name, results in self.rawresults.iteritems():
            for result in results:
                try:
                    title = result['title']
                    txt = result[item]
                except KeyError:
                    continue
                if search in title.lower():
                    if txt:
                        res.append("%s: %s" % (name, txt))
        return res

    def all(self, name, item):
        res = []
        for result in self.rawresults[name]:
            try:
                txt = result[item]
            except KeyError:
                continue
            if txt:
                res.append(txt)
        return res

watcher = Rsswatcher(datadir + os.sep + 'old' + os.sep + 'rss')

def init(): 
    """ called after plugin import """
    thr.start_new_thread(watcher.startwatchers, ())
    return 1

def size():
    """ return number of watched rss entries """
    return watcher.size()

def shutdown():
    """ called before plugin import """
    watcher.stopwatchers()
    return 1
    
def handle_rssadd(bot, ievent):
    """ rss-add <name> <url> .. add a rss item """
    try:
        (name, url) = ievent.args
    except ValueError:
        ievent.missing('<name> <url>')
        return
    watcher.add(name, url)
    ievent.reply('rss item added')

cmnds.add('rss-add', handle_rssadd, 'OPER')
examples.add('rss-add', 'rss-add <name> <url> to the rsswatcher', 'rss-add \
gozerbot http://gozerbot.org/hg/gozerbot/?cmd=changelog;style=rss')

def handle_rssdel(bot, ievent):
    """ rss-del <name> .. delete a rss item """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    if watcher.byname(name):
        watcher.stopwatch(name)
        watcher.delete(name)
        ievent.reply('rss item deleted')
    else:
        ievent.reply('there is no %s rss item' % name)

cmnds.add('rss-del', handle_rssdel, 'OPER')
examples.add('rss-del', 'rss-del <name> .. remove <name> from the \
rsswatcher', 'rss-del mekker')

def handle_rsswatch(bot, ievent):
    """ rss-watch <name> .. start watcher thread """
    if not ievent.channel:
        ievent.reply('no channel provided')
    try:
        name, sleepsec = ievent.args
    except ValueError:
        try:
            name = ievent.args[0]
            sleepsec = 1800
        except IndexError:
            ievent.missing('<name> [secondstosleep]')
            return
    try:
        sleepsec = int(sleepsec)
    except ValueError:
        ievent.reply("time to sleep needs to be in seconds")
        return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss object" % name)
        return
    got = None
    if not rssitem.running:
        rssitem.sleeptime = sleepsec
        rssitem.running = 1
        rssitem.stoprunning = 0
        got = True
        watcher.save()
        try:
            watcher.watch(name)
        except Exception, ex:
            ievent.reply(str(ex))
            return
    if got:
        watcher.save()
        ievent.reply('watcher started')
    else:
        ievent.reply('already watching %s' % name)

cmnds.add('rss-watch', handle_rsswatch, 'OPER')
examples.add('rss-watch', 'rss-watch <name> [seconds to sleep] .. go \
watching <name>', '1) rss-watch gozerbot 2) rss-watch gozerbot 600')

def handle_rssstart(bot, ievent):
    """ rss-start <name> .. start a rss feed to a user """
    if not ievent.rest:
       ievent.missing('<feed name>')
       return
    name = ievent.rest
    rssitem = watcher.byname(name)
    if bot.jabber:
        target = ievent.userhost
    else:
        if users.allowed(ievent.userhost, ['OPER', ]) and not ievent.msg:
            target = ievent.channel
        else:
            target = ievent.nick
    if rssitem == None:
        ievent.reply("we don't have a %s rss object" % name)
        return
    if not rssitem.running:
        ievent.reply('%s watcher is not running' % name)
        return
    if (bot.name, target) in rssitem.watchchannels:
        ievent.reply('we are already monitoring %s on (%s,%s)' % \
(name, bot.name, target))
        return
    rssitem.watchchannels.append((bot.name, target))
    for item in rssitem.itemslist:
        watcher.itemslists.adduniq((name, target), item)
    watcher.save()
    ievent.reply('%s started' % name)

cmnds.add('rss-start', handle_rssstart, ['RSS', 'USER'])
examples.add('rss-start', 'rss-start <name> .. start a rss feed \
(per user/channel) ', 'rss-start gozerbot')

def handle_rssstop(bot, ievent):
    """ rss-start <name> .. start a rss feed to a user """
    if not ievent.rest:
       ievent.missing('<feed name>')
       return
    name = ievent.rest
    rssitem = watcher.byname(name)
    if bot.jabber:
        target = ievent.userhost
    else:
        if users.allowed(ievent.userhost, ['OPER', ]) and not ievent.msg:
            target = ievent.channel
        else:
            target = ievent.nick
    if rssitem == None:
        ievent.reply("we don't have a %s rss feed" % name)
        return
    if not rssitem.running:
        ievent.reply('%s watcher is not running' % name)
        return
    if not (bot.name, target) in rssitem.watchchannels:
        ievent.reply('we are not monitoring %s on (%s,%s)' % \
(name, bot.name, target))
        return
    rssitem.watchchannels.remove((bot.name, target))
    watcher.save()
    ievent.reply('%s stopped' % name)

cmnds.add('rss-stop', handle_rssstop, ['RSS', 'USER'])
examples.add('rss-stop', 'rss-stop <name> .. stop a rss feed \
(per user/channel) ', 'rss-stop gozerbot')

def handle_rsschannels(bot, ievent):
    """ rss-channels <name> .. show channels of rss feed """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing("<name>") 
        return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss object" % name)
        return
    if not rssitem.watchchannels:
        ievent.reply('%s is not in watch mode' % name)
        return
    result = []
    for i in rssitem.watchchannels:
        result.append(str(i))
    ievent.reply("channels of %s: " % name, result, dot=True)

cmnds.add('rss-channels', handle_rsschannels, 'OPER')
examples.add('rss-channels', 'rss-channels <name> .. show channels', \
'rss-channels gozerbot')

def handle_rssaddchannel(bot, ievent):
    """ rss-addchannel <name> [<botname>] <channel> .. add a channel to \
        rss item """
    try:
        (name, botname, channel) = ievent.args
    except ValueError:
        try:
            (name, channel) = ievent.args
            botname = bot.name
        except ValueError:
            try:
                name = ievent.args[0]
                botname = bot.name
                channel = ievent.channel
            except IndexError:
                ievent.missing('<name> [<botname>] <channel>')
                return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss object" % name)
        return
    if not rssitem.running:
        ievent.reply('%s watcher is not running' % name)
        return
    if (botname, channel) in rssitem.watchchannels:
        ievent.reply('we are already monitoring %s on (%s,%s)' % \
(name, botname, channel))
        return
    rssitem.watchchannels.append((botname, channel))
    watcher.save()
    ievent.reply('%s added to %s rss item' % (channel, name))

cmnds.add('rss-addchannel', handle_rssaddchannel, 'OPER')
examples.add('rss-addchannel', 'rss-addchannel <name> [<botname>] <channel> \
..add <channel> or <botname> <channel> to watchchannels of <name>', \
'1) rss-addchannel gozerbot #dunkbots 2) rss-addchannel gozerbot main \
#dunkbots')

def handle_rssadditem(bot, ievent):
    try:
        (name, item) = ievent.args
    except ValueError:
        ievent.missing('<name> <item>')
        return
    if bot.jabber or users.allowed(ievent.userhost, ['OPER', ]):
        target = ievent.channel.lower()
    else:
        target = ievent.nick.lower()
    if not watcher.byname(name):
        ievent.reply("we don't have a %s feed" % name)
        return
    watcher.itemslists.adduniq((name, target), item)
    watcher.itemslists.save()
    #watcher.sync(name)
    ievent.reply('%s added to (%s,%s) itemslist' % (item, name, target))

cmnds.add('rss-additem', handle_rssadditem, ['RSS', 'USER'])
examples.add('rss-additem', 'add a token to the itemslist (per user/channel)',\
 'rss-additem gozerbot link')

def handle_rssdelitem(bot, ievent):
    try:
        (name, item) = ievent.args
    except ValueError:
        ievent.missing('<name> <item>')
        return
    if users.allowed(ievent.userhost, ['OPER', 'RSS']):
        target = ievent.channel.lower()
    else:
        target = ievent.nick.lower()
    if not watcher.byname(name):
        ievent.reply("we don't have a %s feed" % name)
        return
    try:
        watcher.itemslists.remove((name, target), item)
        watcher.itemslists.save()
    except RssNoSuchItem:
        ievent.reply("we don't have a %s rss feed" % name)
        return
    ievent.reply('%s removed from (%s,%s) itemslist' % (item, name, target))

cmnds.add('rss-delitem', handle_rssdelitem, ['RSS', 'USER'])
examples.add('rss-delitem', 'remove a token from the itemslist \
(per user/channel)', 'rss-delitem gozerbot link')

def handle_rssmarkup(bot, ievent):
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    if users.allowed(ievent.userhost, ['OPER', ]):
        target = ievent.channel.lower()
    else:
        target = ievent.nick.lower()
    try:
        ievent.reply(str(watcher.markup[(name, target)]))
    except KeyError:
        pass

cmnds.add('rss-markup', handle_rssmarkup, ['RSS', 'USER'])
examples.add('rss-markup', 'show markup list for a feed (per user/channel)', \
'rss-markup gozerbot')

def handle_rssaddmarkup(bot, ievent):
    try:
        (name, item, value) = ievent.args
    except ValueError:
        ievent.missing('<name> <item> <value>')
        return
    if users.allowed(ievent.userhost, ['OPER', ]):
        target = ievent.channel.lower()
    else:
        target = ievent.nick.lower()
    try:
        watcher.markup.set((name, target), item, value)
        watcher.markup.save()
        ievent.reply('%s added to (%s,%s) markuplist' % (item, name, target))
    except KeyError:
        ievent.reply("no (%s,%s) feed available" % (name, target))

cmnds.add('rss-addmarkup', handle_rssaddmarkup, ['RSS', 'USER'])
examples.add('rss-addmarkup', 'add a markup option to the markuplist \
(per user/channel)', 'rss-addmarkup gozerbot noseperator 1')

def handle_rssdelmarkup(bot, ievent):
    try:
        (name, item) = ievent.args
    except ValueError:
        ievent.missing('<name> <item>')
        return
    if users.allowed(ievent.userhost, ['OPER', 'RSS']):
        target = ievent.channel.lower()
    else:
        target = ievent.nick.lower()
    try:
        del watcher.markup[(name, target)][item]
    except (KeyError, TypeError):
        ievent.reply("can't remove %s from %s feed's markup" %  (item, name))
        return
    watcher.markup.save()
    ievent.reply('%s removed from (%s,%s) markuplist' % (item, name, target))

cmnds.add('rss-delmarkup', handle_rssdelmarkup, ['RSS', 'USER'])
examples.add('rss-delmarkup', 'remove a markup option from the markuplist \
(per user/channel)', 'rss-delmarkup gozerbot noseperator')

def handle_rssdelchannel(bot, ievent):
    """ rss-delchannel <name> [<botname>] <channel> .. delete channel \
        from rss item """
    botname = None
    try:
        (name, botname, channel) = ievent.args
    except ValueError:
        try:
            (name, channel) = ievent.args
            botname = bot.name
        except ValueError:
            try:
                name = ievent.args[0]
                botname = bot.name
                channel = ievent.channel
            except IndexError:
                ievent.missing('<name> [<botname>] [<channel>]')
                return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss object" % name)
        return
    if (botname, channel) not in rssitem.watchchannels:
        ievent.reply('we are not monitoring %s on (%s,%s)' % \
(name, botname, channel))
        return
    rssitem.watchchannels.remove((botname, channel))
    ievent.reply('%s removed from %s rss item' % (channel, name))
    watcher.save()

cmnds.add('rss-delchannel', handle_rssdelchannel, 'OPER')
examples.add('rss-delchannel', 'rss-delchannel <name> [<botname>] \
[<channel>] .. delete <channel> or <botname> <channel> from watchchannels of \
<name>', '1) rss-delchannel gozerbot #dunkbots 2) rss-delchannel gozerbot \
main #dunkbots')

def handle_rssstopwatch(bot, ievent):
    """ rss-stopwatch <name> .. stop a watcher thread """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    rssitem = watcher.byname(name)
    if not rssitem:
        ievent.reply("there is no %s rssitem" % name)
        return
    if not watcher.stopwatch(name):
        ievent.reply("can't stop %s watcher" % name)
        return
    ievent.reply('stopped %s rss watch' % name)

cmnds.add('rss-stopwatch', handle_rssstopwatch, 'OPER')
examples.add('rss-stopwatch', 'rss-stopwatch <name> .. stop polling <name>', \
'rss-stopwatch gozerbot')

def handle_rsssleeptime(bot, ievent):
    """ rss-sleeptime <name> .. get sleeptime of rss item """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss item" % name)
        return
    try:
        ievent.reply('sleeptime for %s is %s seconds' % (name, \
str(rssitem.sleeptime)))
    except AttributeError:
        ievent.reply("can't get sleeptime for %s" % name)

cmnds.add('rss-sleeptime', handle_rsssleeptime, 'OPER')
examples.add('rss-sleeptime', 'rss-sleeptime <name> .. get sleeping time \
for <name>', 'rss-sleeptime gozerbot')

def handle_rsssetsleeptime(bot, ievent):
    """ rss-setsleeptime <name> <seconds> .. set sleeptime of rss item """
    try:
        (name, sec) = ievent.args
        sec = int(sec)
    except ValueError:
        ievent.missing('<name> <seconds>')
        return
    if sec < 60:
        ievent.reply('min is 60 seconds')
        return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss item" % name)
        return
    rssitem.sleeptime = sec
    if rssitem.running:
        watcher.changeinterval(name, sec)
    watcher.save()
    ievent.reply('sleeptime set')

cmnds.add('rss-setsleeptime', handle_rsssetsleeptime, 'OPER')
examples.add('rss-setsleeptime', 'rss-setsleeptime <name> <seconds> .. set \
sleeping time for <name> .. min 60 sec', 'rss-setsleeptime gozerbot 600')

def handle_rssget(bot, ievent):
    """ rss-get <name> .. fetch rss data """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    rssitem = watcher.byname(name)
    if rssitem == None:
        ievent.reply("we don't have a %s rss item" % name)
        return
    try:
        result = watcher.getdata(name)
    except Exception, ex:
        ievent.reply('%s error: %s' % (name, str(ex)))
        return
    response = watcher.makeresponse(name, result, ievent.channel)
    if response:
        ievent.reply("results of %s: %s" % (name, response))
    else:
        ievent.reply("can't match watcher data")

cmnds.add('rss-get', handle_rssget, ['RSS', 'USER', 'ANON'])
examples.add('rss-get', 'rss-get <name> .. get data from <name>', \
'rss-get gozerbot')

def handle_rssrunning(bot, ievent):
    """ rss-running .. show which watchers are running """
    result = watcher.runners()
    resultlist = []
    teller = 1
    for i in result:
        resultlist.append("%s %s" % (i[0], i[1]))
    if resultlist:
        ievent.reply("running rss watchers: ", resultlist, nr=1)
    else:
        ievent.reply('nothing running yet')

cmnds.add('rss-running', handle_rssrunning, ['RSS', 'USER'])
examples.add('rss-running', 'rss-running .. get running rsswatchers', \
'rss-running')

def handle_rsslist(bot, ievent):
    """ rss-list .. return list of rss items """
    result = watcher.list()
    result.sort()
    if result:
        ievent.reply("rss items: ", result, dot=True)
    else:
        ievent.reply('no rss items yet')

cmnds.add('rss-list', handle_rsslist, ['RSS', 'USER', 'ANON'])
examples.add('rss-list', 'get list of rss items', 'rss-list')

def handle_rssurl(bot, ievent):
    """ rss-url <name> .. return url of rss item """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    result = watcher.url(name)
    ievent.reply('url of %s: %s' % (name, result))

cmnds.add('rss-url', handle_rssurl, ['RSS', 'USER', 'ANON'])
examples.add('rss-url', 'rss-url <name> .. get url from rssitem with \
<name>', 'rss-url gozerbot')

def handle_rssitemslist(bot, ievent):
    """ rss-itemslist <name> .. show itemslist of rss item """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    try:
        itemslist = watcher.itemslists[(name, ievent.channel.lower())]
    except KeyError:
        ievent.reply("no itemslist set for (%s, %s)" % (name, \
ievent.channel.lower()))
        return
    ievent.reply("itemslist of (%s, %s): " % (name, ievent.channel.lower()), \
itemslist, dot=True)

cmnds.add('rss-itemslist', handle_rssitemslist, ['RSS', 'USER'])
examples.add('rss-itemslist', 'rss-itemslist <name> .. get itemslist of \
<name> ', 'rss-itemslist gozerbot')

def handle_rssscan(bot, ievent):
    """ rss-scan <name> .. scan rss item for used xml items """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    if not watcher.byname(name):
        ievent.reply('no %s feeds available' % name)
        return
    try:
        result = watcher.scan(name)
    except Exception, ex:
        ievent.reply(str(ex))
        return
    if result == None:
        ievent.reply("can't get data for %s" % name)
        return
    res = []
    for i in result:
        res.append("%s=%s" % i)
    ievent.reply("tokens of %s: " % name, res)

cmnds.add('rss-scan', handle_rssscan, 'OPER')
examples.add('rss-scan', 'rss-scan <name> .. get possible items of <name> ', \
'rss-scan gozerbot')

def handle_rsssync(bot, ievent):
    """ rss-sync <name> .. sync rss item data """
    try:
        name = ievent.args[0]
    except IndexError:
        ievent.missing('<name>')
        return
    try:
        result = watcher.sync(name)
        ievent.reply('%s synced' % name)
    except Exception, ex:
        ievent.reply("%s error: %s" % (name, str(ex)))

cmnds.add('rss-sync', handle_rsssync, 'OPER')
examples.add('rss-sync', 'rss-sync <name> .. sync data of <name>', \
'rss-sync gozerbot')

def handle_rssfeeds(bot, ievent):
    """ rss-feeds <channel> .. show what feeds are running in a channel """
    try:
        channel = ievent.args[0]
    except IndexError:
        channel = ievent.printto
    try:
        result = watcher.feeds(bot.name, channel)
        if result:
            ievent.reply("feeds running in %s: " % channel, result, dot=True)
        else:
            ievent.reply('%s has no feeds running' % channel)
    except Exception, ex:
        ievent.reply("ERROR: %s" % str(ex))

cmnds.add('rss-feeds', handle_rssfeeds, ['USER', 'RSS'])
examples.add('rss-feeds', 'rss-feeds <name> .. show what feeds are running \
in a channel', '1) rss-feeds 2) rss-feeds #dunkbots')

def handle_rsslink(bot, ievent):
    try:
        feed, rest = ievent.rest.split(' ', 1)
    except ValueError:
        ievent.missing('<feed> <words to search>')
        return
    rest = rest.strip().lower()
    try:
        res = watcher.search(feed, 'link', rest)
        if not res:
            res = watcher.search(feed, 'feedburner:origLink', rest)
        if res:
            ievent.reply(res, dot=" \002||\002 ")
    except KeyError:
        ievent.reply('no %s feed data available' % feed)
        return

cmnds.add('rss-link', handle_rsslink, ['RSS', 'USER'])
examples.add('rss-link', 'give link of item which title matches search key', \
'rss-link gozerbot gozer')

def handle_rssdescription(bot, ievent):
    try:
        feed, rest = ievent.rest.split(' ', 1)
    except ValueError:
        ievent.missing('<feed> <words to search>')
        return
    rest = rest.strip().lower()
    res = ""
    try:
        ievent.reply(watcher.search(feed, 'description', rest), \
dot=" \002||\002 ")
    except KeyError:
        ievent.reply('no %s feed data available' % feed)
        return

cmnds.add('rss-description', handle_rssdescription, ['RSS', 'USER'])
examples.add('rss-description', 'give description of item which title \
matches search key', 'rss-description gozerbot gozer')

def handle_rssall(bot, ievent):
    try:
        feed = ievent.args[0]
    except IndexError:
        ievent.missing('<feed>')
        return
    try:
        ievent.reply(watcher.all(feed, 'title'), dot=" \002||\002 ")
    except KeyError:
        ievent.reply('no %s feed data available' % feed)
        return

cmnds.add('rss-all', handle_rssall, ['RSS', 'USER'])
examples.add('rss-all', "give titles of a feed", 'rss-all gozerbot')

def handle_rsssearch(bot, ievent):
    try:
        txt = ievent.args[0]
    except IndexError:
        ievent.missing('<txt>')
        return
    try:        
        ievent.reply(watcher.searchall('title', txt), dot=" \002||\002 ")
    except KeyError:
        ievent.reply('no %s feed data available' % feed)
        return

cmnds.add('rss-search', handle_rsssearch, ['RSS', 'USER'])
examples.add('rss-search', "search titles of all current feeds", \
'rss-search goz')

def handle_rssdump(bot, ievent):
    try:
        ievent.reply(str(watcher.rawresults[ievent.rest]))
    except Exception, ex:
        ievent.reply(str(ex))

cmnds.add('rss-dump', handle_rssdump, 'OPER')
examples.add('rss-dump', 'dump cached rss data', 'rss-dump')
