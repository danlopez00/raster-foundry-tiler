package com.azavea.rasterfoundry

import scopt._
import com.typesafe.config._

object Config {
  val props = ConfigFactory.load()
}

case class Config (
  chunkerResult: String = "",
  statusQueue: String = Config.props.getString("rf.statusSqsQueueUrl")
)


object ConfigParser extends OptionParser[Config]("scopt") {
  head("mosaic", "0.1")

  arg[String]("<chunker result>") required() action { (x, c) => c.copy(chunkerResult = x) } text("URL or local path of chunker json output")

  opt[String]("status-queue") optional() action { (x, c) => c.copy(statusQueue = x) } text("SQS URL")
}
